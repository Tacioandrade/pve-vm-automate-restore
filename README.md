# Validação de Integridade de VMs para PVE 🚀


Esta aplicação automatiza a restauração de máquinas virtuais (VMs) e containers (LXC) a partir de um **Proxmox Backup Server (PBS)** para um nó **Proxmox VE (PVE)**, validando a integridade dos backups através de boot e análise por IA.

## ✨ Novas Funcionalidades (v2.0)
- **Restauração Paralela**: Agora suporta múltiplas restaurações simultâneas via `MAX_WORKERS`.
- **Análise por IA**: Integração com **Google Gemini** para analisar screenshots e confirmar se o SO subiu corretamente.
- **Arquitetura Modular**: Código refatorado em pacotes para melhor manutenção.
- **Interface CLI**: Novos argumentos para maior flexibilidade sem editar o `.env`.
- **Sistema de Logs**: Logs estruturados com rotação automática em `logs/restauracao.log`.
- **Resiliência**: Mecanismo de Retry com Backoff exponencial para chamadas de rede e API.
- **Modo Dry-Run**: Simule todo o processo sem realizar alterações reais no Proxmox.

## 🛠️ Requisitos
- Python 3.10+
- Acesso à API do Proxmox VE.
- Chaves SSH configuradas para o nó Proxmox (necessário para screenshots).
- Google Gemini API Key (opcional, para análise de imagem).
- Bot do Telegram (opcional, para relatórios).

## 🚀 Instalação e Configuração

1. **Clonar e instalar dependências:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configurar variáveis de ambiente:**
   ```bash
   cp .env.example .env
   # Edite as variáveis no .env
   ```

3. **Lista de VMs:**
   Crie um arquivo `vms.txt` com um VMID por linha. O script utiliza um sistema **Round-Robin**, movendo a VM processada para o final da lista após cada execução.

## 💻 Uso (CLI)

```bash
# Execução padrão (usa valores do .env)
python main.py

# Restaurar 5 VMs específicas de um arquivo customizado
python main.py --count 5 --vms lista_especial.txt

# Executar em modo Simulação (Dry-Run)
python main.py --dry-run

# Desativar IA e Telegram para um teste rápido
python main.py --no-ia --no-telegram
```

### Argumentos Disponíveis:
- `--count N`: Quantidade de VMs a processar.
- `--vms FILE`: Caminho do arquivo de lista de VMs.
- `--dry-run`: Ativa modo de simulação.
- `--no-ia`: Pula a análise do Google Gemini.
- `--no-telegram`: Não envia relatório ao Telegram.

## 📂 Estrutura do Projeto
- `main.py`: Orquestrador principal e interface CLI.
- `pbs_restore/`: Core da aplicação.
    - `config.py`: Carregamento e validação de configurações.
    - `proxmox_client.py`: Integração com a API Proxmox e lógica de polling.
    - `gemini.py`: Integração com Google Gemini AI.
    - `screenshot.py`: Captura de tela via Monitor QEMU e SSH/Local fallback.
    - `report.py`: Gerenciamento de estado e contexto de restauração.
    - `vm_manager.py`: Manipulação do arquivo de lista de VMs.
    - `models.py`: Dataclasses para tipagem forte.
    - `logging_config.py`: Configuração de logs rotativos.
    - `exceptions.py`: Hierarquia de erros customizados.

## 📊 Relatórios
Ao final de cada execução, um relatório detalhado é enviado via Telegram contendo:
- Resumo de sucessos, falhas e ignoradas.
- Tempo exato de restauração de cada máquina.
- Resultado da análise visual da IA (ex: "Boot OK").
- Alertas de backups antigos (> 15 dias).

---
Desenvolvido para garantir a paz de espírito no seu Plano de Recuperação de Desastres. 🛡️