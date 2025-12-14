document.addEventListener('DOMContentLoaded', () => new ProspektAsistan());

class ProspektAsistan {
    constructor() {
        // Localhost üzerinde çalışıyoruz
        this.API_BASE = 'http://localhost:8000';
        this.init();
    }

    /**
     * Uygulamayı başlatır
     */
    init() {
        this.cacheDOMElements();
        this.setupEventListeners();
    }

    /**
     * DOM elementlerini önbelleğe alır
     */
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

    /**
     * Event listener'ları kurar
     */
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
             this.addAssistantMessage("Merhaba! Ben ProspektAsistan. Size ilaçlar hakkında nasıl yardımcı olabilirim?");
        }
    }

    hideChat() { this.els.chatWrapper.classList.remove('visible'); }

    async sendMessage() {
        const message = this.els.messageInput.value.trim();
        if (!message) return;

        this.addUserMessage(message);
        this.els.messageInput.value = '';
        this.els.typingIndicator.style.display = 'flex';
        this.scrollChat();

        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 65000); 

            const response = await fetch(`${this.API_BASE}/search`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: message }),
                signal: controller.signal
            });
            
            clearTimeout(timeoutId);

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || 'Sunucudan bir hata alındı.');
            }
            
            const data = await response.json();
            this.addAssistantMessage(data.llm_answer);

        } catch (error) {
            console.error('Hata:', error);
            if (error.name === 'AbortError') {
                 this.addAssistantMessage('Üzgünüm, cevap almak çok uzun sürdü. Lütfen daha sonra tekrar deneyin.');
            } else {
                 this.addAssistantMessage(`Üzgünüm, bir hata oluştu: ${error.message}`);
            }
        } finally {
            this.els.typingIndicator.style.display = 'none';
        }
    }

    addUserMessage(content) { this.createMessageElement(content, 'user'); }

    addAssistantMessage(content) {
        const formattedContent = content.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n- /g, '<br>• ').replace(/\n\* /g, '<br>• ').replace(/\n/g, '<br>');
        this.createMessageElement(formattedContent, 'assistant');
    }

    createMessageElement(content, type) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}-message`;
        const avatarIcon = type === 'user' ? 'fa-user' : 'fa-robot';
        messageDiv.innerHTML = `<div class="message-avatar"><i class="fas ${avatarIcon}"></i></div><div class="message-content">${content}</div>`;
        this.els.chatMessages.appendChild(messageDiv);
        this.scrollChat();
    }

    scrollChat() { this.els.chatMessages.scrollTop = this.els.chatMessages.scrollHeight; }
}