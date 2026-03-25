document.addEventListener('DOMContentLoaded', () => new ProspektAsistan());

class ProspektAsistan {
    constructor() {
        this.API_BASE = 'http://localhost:8000';
        this.init();
    }

    init() {
        this.cacheDOMElements();
        this.setupEventListeners();
    }

    // Türkçe Title Case dönüştürücü
    toTurkishTitleCase(text) {
        if (!text) return '';
        
        // Önce küçük harfe çevir (Türkçe karakterler için)
        const lower = text.toLowerCase()
            .replace(/I/g, 'ı')
            .replace(/İ/g, 'i');
        
        // Kelimeleri ayır ve her birinin baş harfini büyük yap
        const abbreviations = ['mg', 'ml', 'mcg', 'iu', 'gr', 'kg', 'g', 'l'];
        
        return lower.split(' ').map((word, index) => {
            // Sayı ise olduğu gibi
            if (/^\d+$/.test(word)) return word;
            // Kısaltma ise küçük
            if (abbreviations.includes(word)) return word;
            // Türkçe baş harf büyütme
            if (!word) return word;
            let first = word[0];
            if (first === 'i') first = 'İ';
            else if (first === 'ı') first = 'I';
            else first = first.toUpperCase();
            return first + word.slice(1);
        }).join(' ');
    }

    cacheDOMElements() {
        this.els = {
            chatWrapper: document.getElementById('chatWrapper'),
            startChatCard: document.getElementById('startChatCard'),
            homeBtn: document.getElementById('homeBtn'),
            sendBtn: document.getElementById('sendBtn'),
            messageInput: document.getElementById('messageInput'),
            chatMessages: document.getElementById('chatMessages'),
            typingIndicator: document.getElementById('typingIndicator'),
        };
    }

    setupEventListeners() {
        this.els.startChatCard.addEventListener('click', () => this.showChat());
        this.els.homeBtn.addEventListener('click', () => this.hideChat());
        this.els.sendBtn.addEventListener('click', () => this.sendMessage());
        this.els.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.sendMessage();
        });
    }

    showChat() {
        this.els.chatWrapper.classList.add('visible');
        if (this.els.chatMessages.children.length === 0) {
            this.addAssistantMessage("Merhaba! Ben ProspektAsistan. İlaçlarla ilgili sorunuzu yazın, kaynaklı yanıt vereyim.");
        }
    }

    hideChat() { this.els.chatWrapper.classList.remove('visible'); }

    async sendMessage(text = null) {
        let message = text || this.els.messageInput.value.trim();
        if (!message) return;

        if (!text) {
            this.addUserMessage(message);
            this.els.messageInput.value = '';
        }

        this.els.typingIndicator.style.display = 'flex';
        this.scrollChat();

        try {
            const response = await fetch(`${this.API_BASE}/rag/ask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: message, top_k: 8 })
            });

            if (response.ok) {
                const data = await response.json();
                this.handleResponse(data);
            } else {
                // Geriye donuk uyumluluk: eski endpoint
                const fallbackResp = await fetch(`${this.API_BASE}/search`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: message })
                });
                const fallbackData = await fallbackResp.json();
                this.handleResponse(fallbackData);
            }

        } catch (error) {
            console.error('Hata:', error);
            this.addAssistantMessage("Bağlantı hatası oluştu. Lütfen sunucunun çalıştığından emin olun.");
        } finally {
            this.els.typingIndicator.style.display = 'none';
        }
    }

    async fetchSection(drugId, sectionNumber) {
        this.els.typingIndicator.style.display = 'flex';
        try {
            const response = await fetch(`${this.API_BASE}/search`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: "FETCH_SECTION", drug_id: drugId, section_number: sectionNumber })
            });
            const data = await response.json();
            
            if (data.type === 'section_content') {
                this.addAssistantMessage(`<h3>${data.title}</h3><p>${data.content}</p>`);
            } else {
                this.addAssistantMessage("İçerik alınamadı.");
            }
        } catch (e) {
            this.addAssistantMessage("Hata oluştu.");
        } finally {
            this.els.typingIndicator.style.display = 'none';
        }
    }

    handleResponse(data) {
        if (data.type === 'error') {
            this.addAssistantMessage(data.message);
        }
        else if (data.type === 'rag') {
            const answerHtml = this.formatRagAnswer(data.answer || 'Yanıt üretilemedi.');
            const sourcesHtml = this.renderSources(data.sources || []);
            this.addAssistantMessage(`
                <div class="rag-answer">${answerHtml}</div>
                ${sourcesHtml}
            `);
        }
        else if (data.type === 'list') {
            const container = document.createElement('div');
            container.innerHTML = `<div class="message-content"><p>'${data.items[0]?.brand || 'Arama'}' ile eşleşen ilaçlar:</p></div>`;
            
            const listDiv = document.createElement('div');
            listDiv.className = 'drug-list-container';
            
            data.items.forEach(item => {
                const card = document.createElement('div');
                card.className = 'drug-card';
                
                // Kart içeriği
                const ingredientText = item.active_ingredient 
                    ? item.active_ingredient.substring(0, 60) + (item.active_ingredient.length > 60 ? '...' : '')
                    : '';
                
                card.innerHTML = `
                    <div class="drug-card-icon">
                        <i class="fas fa-pills"></i>
                    </div>
                    <div class="drug-card-content">
                        <div class="drug-card-brand">${item.brand || item.name}</div>
                        <div class="drug-card-details">
                            ${item.dosage ? `<span class="drug-card-dosage">${item.dosage}</span>` : ''}
                            ${item.form ? `<span class="drug-card-form">${item.form}</span>` : ''}
                        </div>
                        ${ingredientText ? `<div class="drug-card-ingredient">${ingredientText}</div>` : ''}
                    </div>
                    <i class="fas fa-chevron-right drug-card-arrow"></i>
                `;
                
                card.onclick = () => this.sendMessage(item.name);
                listDiv.appendChild(card);
            });
            
            this.addMessageElement(container, 'assistant');
            this.els.chatMessages.lastChild.appendChild(listDiv);
        } 
        else if (data.type === 'detail') {
            // İlaç ismini Title Case'e çevir
            const drugNameFormatted = this.toTurkishTitleCase(data.drug_name);
            
            const html = `
                <div class="drug-detail-card">
                    <h2>${drugNameFormatted}</h2>
                    <div class="summary-box">
                        <strong>Özet Bilgi:</strong>
                        <p>${data.summary}</p>
                    </div>
                    <div class="section-buttons">
                        <p>Detaylı bilgi için başlıklara tıklayın:</p>
                    </div>
                </div>
            `;
            
            const container = document.createElement('div');
            container.innerHTML = html;
            
            const btnContainer = container.querySelector('.section-buttons');
            data.sections.forEach(sec => {
                const btn = document.createElement('button');
                btn.className = 'section-btn';
                btn.innerText = sec.title;
                btn.onclick = () => this.fetchSection(data.drug_id, sec.id);
                btnContainer.appendChild(btn);
            });

            this.addMessageElement(container, 'assistant');
        }
    }

    formatRagAnswer(text) {
        const escaped = (text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        return escaped
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>');
    }

    renderSources(sources) {
        if (!sources.length) return '';

        const items = sources.map((s) => {
            const score = typeof s.score === 'number' ? s.score.toFixed(3) : s.score;
            return `
                <div class="source-item">
                    <div class="source-title">${s.drug_name || 'Bilinmeyen ilaç'} - B${s.bolum_no || '-'}</div>
                    <div class="source-meta">Skor: ${score} | ${s.section_title || 'Başlık yok'}</div>
                </div>
            `;
        }).join('');

        return `
            <div class="rag-sources">
                <div class="rag-sources-title">Kullanılan Kaynaklar</div>
                ${items}
            </div>
        `;
    }

    addUserMessage(content) { 
        const div = document.createElement('div');
        div.className = 'message-content';
        div.innerText = content;
        this.addMessageElement(div, 'user'); 
    }

    addAssistantMessage(content) {
        const div = document.createElement('div');
        div.className = 'message-content';
        div.innerHTML = content;
        this.addMessageElement(div, 'assistant');
    }

    addMessageElement(contentDiv, type) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}-message`;
        
        // Kullanıcı için user ikonu, asistan için medikal ikon
        const avatarIcon = type === 'user' ? 'fa-user' : 'fa-file-medical';
        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'message-avatar';
        avatarDiv.innerHTML = `<i class="fas ${avatarIcon}"></i>`;
        
        messageDiv.appendChild(avatarDiv);
        
        if (typeof contentDiv === 'string') {
            const c = document.createElement('div');
            c.className = 'message-content';
            c.innerHTML = contentDiv;
            messageDiv.appendChild(c);
        } else {
            if (!contentDiv.classList.contains('message-content') && !contentDiv.querySelector('.drug-detail-card')) {
                contentDiv.classList.add('message-content');
            }
            messageDiv.appendChild(contentDiv);
        }

        this.els.chatMessages.appendChild(messageDiv);
        this.scrollChat();
    }

    scrollChat() { this.els.chatMessages.scrollTop = this.els.chatMessages.scrollHeight; }
}
