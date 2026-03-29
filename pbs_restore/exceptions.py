class PBSRestoreError(Exception):
    """Exceção base para todas as exceções da aplicação."""

class ConfigurationError(PBSRestoreError):
    """Configuração inválida ou ausente no .env."""

class ProxmoxConnectionError(PBSRestoreError):
    """Falha de conexão ou autenticação com a API Proxmox."""

class BackupNotFoundError(PBSRestoreError):
    """Nenhum backup encontrado para a VM especificada."""

class RestoreError(PBSRestoreError):
    """Falha durante o processo de restauração de uma VM."""

class ScreenshotError(PBSRestoreError):
    """Falha ao capturar ou processar screenshot."""

class GeminiAnalysisError(PBSRestoreError):
    """Falha na análise com Gemini API."""

class TelegramError(PBSRestoreError):
    """Falha ao enviar mensagem via Telegram."""

class VMFileError(PBSRestoreError):
    """Erro ao ler ou manipular o arquivo vms.txt."""
