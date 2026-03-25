# Aplicação para Automação de Restauração de VMs para validação de integridade

Esta aplicação automatiza a restauração de backups de máquinas virtuais a partir de um Proxmox Backup Server (PBS) para um nó Proxmox VE (PVE).
O sistema lê uma lista de máquinas virtuais/containers, busca os backups mais recentes de cada uma, as restaura (sobrescrevendo se necessário) e envia um relatório de status para um grupo no Telegram.

## Funcionalidades
- Carrega as configurações de acesso armazenadas em um arquivo `.env`.
- Lê uma lista alvo de VMs/containers a partir do arquivo `vms.txt` e processa uma quantidade específica definida antes da execução.
- Localiza automaticamente o backup mais recente de cada VM/container especificado.
- Restaura as VMs/containers em um "Storage" predeterminado.
- Envia um relatório prático formatado em Markdown diretamente para o Telegram.

## Requisitos
- Python 3.7+
- Proxmox VE com acesso à API habilitado
- Um Token de Bot do Telegram e o ID do Grupo (Chat ID) desejado

## Preparação do Ambiente

1. **Instale as dependências:**
   Recomenda-se a criação de um ambiente virtual Python isolado.
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure as variáveis de ambiente:**
   Copie o template e crie seu arquivo real de ambiente:
   ```bash
   cp .env.example .env
   ```
   Edite o arquivo `.env` e preencha com as informações do seu ambiente:
   - `PROXMOX_URL`: O endereço para acessar a interface web do Proxmox (exemplo: `https://192.168.1.100:8006`).
   - `PROXMOX_NODE`: O nome do nó do Proxmox responsável pelas operações (exemplo: `pve`).
   - `PROXMOX_USER`: O usuário do Proxmox que fará a ação (exemplo: `root@pam`).
   - `PROXMOX_PASSWORD`: A senha vinculada ao usuário.
   - `BACKUP_STORAGE`: O nome dado no PVE referente ao Storage do Proxmox Backup Server.
   - `RESTORE_STORAGE`: O nome do Storage onde a VM recém-restaurada será salva.
   - `VM_RESTORE_COUNT`: Quantidade de VMs (da primeira até a última linha do arquivo `vms.txt`) que deverão ser incluídas no processo.
   - `TELEGRAM_BOT_TOKEN`: A chave de integração do Bot do Telegram, gerada pelo BotFather.
   - `TELEGRAM_CHAT_ID`: O identificador do Chat/Grupo onde ele mandará o relatório da execução.

3. **Indique quais VMs devem voltar:**
   Preencha o arquivo `vms.txt` contendo os IDs dessas VMs, colocando apenas um VMID por linha, correspondente aos backups em seu storage.
   ```text
   100
   101
   ```

## Como Usar

Basta rodar o script principal do Python no mesmo diretório:
```bash
python main.py
```

O script tomará controle de acionar as restaurações da API de uma por vez, aguardando o final para montar as respostas válidas. Não se esqueça de adicionar o seu Bot oficial como administrador ou participante habilitado do grupo apontado no ID do Telegram.

## Agendamento via Crontab (Automático)

Para que a restauração ocorra de forma agendada e automática periodicamente, você pode configurar uma tarefa no `crontab` do Linux. É importante chamar o interpretador Python diretamente de dentro da pasta do ambiente virtual (`venv/bin/python`), para garantir que ele encontre as dependências do projeto, e executar no local onde os arquivos da aplicação estão.

Abra o agendador do cron digitando no terminal:
```bash
crontab -e
```

Adicione uma linha como a do exemplo abaixo. Este exemplo está programado para executar **toda segunda-feira de madrugada, às 02h00**:

```bash
0 2 * * 1 cd /home/tacio/projetos/pve-vm-automate-restore && /home/tacio/projetos/pve-vm-automate-restore/venv/bin/python main.py >> /home/tacio/projetos/pve-vm-automate-restore/restauracao.log 2>&1
```

> **Nota:** Certifique-se de substituir o caminho `/home/tacio/projetos/pve-vm-automate-restore` nas três vezes que aparece pela pasta definitiva caso mova a aplicação de lugar. O trecho ao final (`>> restauracao.log 2>&1`) armazena a saída da execução em um arquivinho de log, sendo excelente para revisar caso a rotina demore ou precise investigar algo futuramente!

## Próximas funcionalidades
- Adicionar alguma forma de integração com alguma IA como o Claude Cowork ou outra que tenha acesso a um navegador para que ele possa acessar a interface web do Proxmox e realizar a validação se o Sistema Operacional subiu ou não após o backup

- Gerar um relatório em HTML exportável para PDF com o status de cada VM restaurada, para que possa ser salvo e entregue como prova de que a restauração foi realizada com sucesso

- Adicionar uma flag no .env que liste o backup de todas as VMs executados nos ultimos 7 dias e que ainda não estão listados no arquivo vms.txt, para que possa ser adicionado automaticamente para homologação futura