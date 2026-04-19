#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KT Parser JSON Çıktılarını SQLite Veritabanına Aktarma Script'i

Bu script, kt_parser_v2.py tarafından üretilen JSON dosyalarını
ilac_prospektus.db SQLite veritabanına aktarır.

KULLANIM:
    python import_parsed_kt.py
    
    # Veya özel ayarlarla:
    python import_parsed_kt.py --input kt_parsed_output --db custom_db.db
"""

import json
import sqlite3
import os
import argparse
from datetime import datetime


class KTDatabaseImporter:
    """JSON çıktılarını SQLite veritabanına aktarma sınıfı"""
    
    def __init__(self, json_dir: str, db_path: str):
        """
        Args:
            json_dir: JSON dosyalarının bulunduğu klasör
            db_path: SQLite veritabanı dosya yolu
        """
        self.json_dir = json_dir
        self.db_path = db_path
        
        self.drugs_json = os.path.join(json_dir, "drugs_metadata.json")
        self.sections_json = os.path.join(json_dir, "drug_sections.json")
        self.failures_json = os.path.join(json_dir, "failed_files.json")
        
        # İstatistikler
        self.imported_drugs = 0
        self.imported_sections = 0
        self.skipped_drugs = 0
    
    def validate_files(self) -> bool:
        """Gerekli dosyaların var olduğunu kontrol et."""
        missing_files = []
        
        if not os.path.exists(self.drugs_json):
            missing_files.append(self.drugs_json)
        if not os.path.exists(self.sections_json):
            missing_files.append(self.sections_json)
        
        if missing_files:
            print("❌ HATA: Şu dosyalar bulunamadı:")
            for f in missing_files:
                print(f"   • {f}")
            print("\nÖnce kt_parser_v2.py script'ini çalıştırmalısınız!")
            return False
        
        return True
    
    def load_json_data(self):
        """JSON dosyalarını yükle."""
        print("📂 JSON dosyaları yükleniyor...")
        
        with open(self.drugs_json, 'r', encoding='utf-8') as f:
            self.drugs_data = json.load(f)
        print(f"   ✅ {len(self.drugs_data)} ilaç metadata'si yüklendi")
        
        with open(self.sections_json, 'r', encoding='utf-8') as f:
            self.sections_data = json.load(f)
        print(f"   ✅ {len(self.sections_data)} bölüm yüklendi")
        
        # Failures (opsiyonel)
        if os.path.exists(self.failures_json):
            with open(self.failures_json, 'r', encoding='utf-8') as f:
                self.failures_data = json.load(f)
            print(f"   ℹ️  {len(self.failures_data)} hatalı dosya kaydı bulundu")
    
    def create_tables_if_not_exist(self, cursor):
        """Tablolar yoksa oluştur (mevcut process_pdfs.py ile uyumlu)."""
        print("🔧 Veritabanı tabloları kontrol ediliyor...")
        
        # Drugs tablosu
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS drugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            active_ingredient TEXT,
            excipients TEXT,
            license_holder TEXT,
            approval_date TEXT,
            pediatric_use TEXT,
            geriatric_use TEXT,
            file_path TEXT UNIQUE,
            is_ocr_required BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Drug Sections tablosu
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS drug_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drug_id INTEGER,
            section_number INTEGER,
            section_title TEXT,
            content TEXT,
            FOREIGN KEY (drug_id) REFERENCES drugs (id)
        )
        ''')
        
        print("   ✅ Tablolar hazır")
    
    def import_data(self):
        """Ana import işlemi."""
        print(f"\n💾 Veritabanına aktarılıyor: {self.db_path}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        self.create_tables_if_not_exist(cursor)
        
        print("\n📥 İlaçlar ve bölümler ekleniyor...")
        
        # Her ilacı işle
        for drug in self.drugs_data:
            try:
                # İlaç zaten var mı kontrol et (file_path'e göre)
                cursor.execute(
                    "SELECT id FROM drugs WHERE file_path = ?",
                    (drug['file_name'],)
                )
                existing = cursor.fetchone()
                
                if existing:
                    drug_id = existing[0]
                    self.skipped_drugs += 1
                    print(f"   ⏭️  Zaten var: {drug['name']} (güncelleniyor)")
                    
                    # Güncelle
                    cursor.execute("""
                        UPDATE drugs SET
                            name = ?,
                            active_ingredient = ?,
                            excipients = ?,
                            license_holder = ?,
                            is_ocr_required = ?
                        WHERE id = ?
                    """, (
                        drug['name'],
                        drug['active_ingredient'],
                        drug['excipients'],
                        drug['license_holder'],
                        drug['is_ocr_required'],
                        drug_id
                    ))
                    
                    # Eski bölümleri sil
                    cursor.execute(
                        "DELETE FROM drug_sections WHERE drug_id = ?",
                        (drug_id,)
                    )
                else:
                    # Yeni ilaç ekle
                    cursor.execute("""
                        INSERT INTO drugs 
                        (name, active_ingredient, excipients, license_holder, 
                         file_path, is_ocr_required)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        drug['name'],
                        drug['active_ingredient'],
                        drug['excipients'],
                        drug['license_holder'],
                        drug['file_name'],
                        drug['is_ocr_required']
                    ))
                    
                    drug_id = cursor.lastrowid
                    self.imported_drugs += 1
                    print(f"   ✅ Eklendi: {drug['name']}")
                
                # Bu ilaca ait bölümleri ekle
                for section in self.sections_data:
                    if section['file_name'] == drug['file_name']:
                        cursor.execute("""
                            INSERT INTO drug_sections 
                            (drug_id, section_number, section_title, content)
                            VALUES (?, ?, ?, ?)
                        """, (
                            drug_id,
                            section['section_number'],
                            section['section_title'],
                            section['content']
                        ))
                        self.imported_sections += 1
                
            except Exception as e:
                print(f"   ❌ HATA ({drug['name']}): {str(e)}")
                conn.rollback()
                continue
        
        # Commit
        conn.commit()
        conn.close()
        
        print("\n✅ Import tamamlandı!")
    
    def print_summary(self):
        """Import özeti."""
        print(f"\n{'='*60}")
        print("📊 İMPORT ÖZETİ")
        print(f"{'='*60}")
        print(f"Yeni Eklenen İlaçlar : {self.imported_drugs}")
        print(f"Güncellenen İlaçlar  : {self.skipped_drugs}")
        print(f"Toplam Bölümler      : {self.imported_sections}")
        print(f"{'='*60}\n")
    
    def run(self):
        """Tüm işlemi çalıştır."""
        print("""
╔════════════════════════════════════════════════════════════╗
║   KT PARSER → SQLITE İMPORT ARACI                          ║
╚════════════════════════════════════════════════════════════╝
        """)
        
        # 1. Dosya validasyonu
        if not self.validate_files():
            return False
        
        # 2. JSON'ları yükle
        self.load_json_data()
        
        # 3. Veritabanına aktar
        self.import_data()
        
        # 4. Özet
        self.print_summary()
        
        return True


def main():
    """Ana fonksiyon"""
    parser = argparse.ArgumentParser(
        description='KT Parser JSON çıktılarını SQLite veritabanına aktar'
    )
    parser.add_argument(
        '--input', '-i',
        default='kt_parsed_output',
        help='JSON dosyalarının bulunduğu klasör (varsayılan: kt_parsed_output)'
    )
    parser.add_argument(
        '--db', '-d',
        default='ilac_prospektus.db',
        help='SQLite veritabanı dosya yolu (varsayılan: ilac_prospektus.db)'
    )
    
    args = parser.parse_args()
    
    # Importer oluştur ve çalıştır
    importer = KTDatabaseImporter(args.input, args.db)
    success = importer.run()
    
    if success:
        print("✨ İşlem başarıyla tamamlandı!")
        print(f"\n💡 Sonraki adım: Embeddings oluşturun")
        print("   python create_embeddings.py")
    else:
        print("\n❌ İşlem başarısız oldu!")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
