import json
import logging
from PIL import Image
from google import genai
from tenacity import retry, wait_exponential, stop_after_attempt
from pbs_restore.exceptions import GeminiAnalysisError

logger = logging.getLogger(__name__)

@retry(
    wait=wait_exponential(multiplier=1, min=4, max=10),
    stop=stop_after_attempt(3),
    reraise=True
)
def analyze_image_with_gemini(image_path, config):

    """Envia a imagem para o Google Gemini API tentando diferentes modelos de fallback."""
    try:
        # Lista de modelos para tentar (do mais provável para o menos provável)
        models_to_try = [
            config.gemini_model,
            "gemini-1.5-flash",
            "gemini-flash-latest",
            "gemini-1.5-flash-001"
        ]
        
        # Remove duplicatas mantendo a ordem
        models_to_try = list(dict.fromkeys(models_to_try))
        
        client = genai.Client(api_key=config.gemini_api_key)

        
        prompt = """
Analise a imagem. Responda APENAS com JSON válido, sem explicações:

{
  "boot_concluido": true/false,
  "estado": "login"|"bloqueio"|"area_trabalho"|"app_aberto"|"shell"|"erro"|"atualizando"|"tela_preta"|"indeterminado",
  "os_detectado": "windows"|"linux"|"indeterminado",
  "confianca": "alta"|"media"|"baixa"
}

BOOT_CONCLUIDO=false se houver: logo de fabricante, POST, "BIOS"/"UEFI", GRUB/bootloader, animação de boot, mensagens de kernel ("[OK]", "Starting", "systemd"), tela de atualização ("Não desligue", "Configurando atualizações"), BSOD/tela azul, "Recuperação"/"Reparo Automático"/"WinRE". Caso contrário: true.

ESTADO (use o primeiro que se aplicar):
- erro: BSOD, tela azul, "Reparo Automático", "Automatic Repair", "Recovery"
- atualizando: "Não desligue", "Configurando atualizações X%", "Windows Update"
- tela_preta: imagem preta ou sem conteúdo identificável
- login: campo de senha, lista de usuários, "Ctrl+Alt+Delete"/"Pressione Ctrl+Alt+Delete", "Sign in", prompt TTY ("login:", "Password:"), tela de login gráfico Linux
- bloqueio: relógio/data em destaque, sem campo de senha visível
- shell: prompt CMD ("C:\\"), PowerShell ("PS C:\\"), bash/zsh ("$","#","user@host")
- area_trabalho: ícones, barra de tarefas/dock, papel de parede sem login/bloqueio
- app_aberto: janela de aplicativo em primeiro plano
- indeterminado: nenhuma categoria aplicável

OS_DETECTADO:
- windows: logo Windows, barra de tarefas with Iniciar, CMD/PowerShell, interface Fluent
- linux: prompt shell Linux, logo de distro, GRUB, GNOME/KDE/XFCE
- indeterminado: não identificável

CONFIANCA: alta=elementos claros / media=parcialmente visíveis / baixa=borrado, recortado ou contraditório
        """
        
        img = Image.open(image_path)
        
        for model_name in models_to_try:
            try:
                logger.debug(f"Tentando modelo {model_name} para {image_path}...")
                response = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, img]
                )
                
                # Tenta extrair the JSON da resposta
                text = response.text.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                
                data = json.loads(text)
                logger.debug(f"Sucesso com o modelo {model_name}. Resultado: {data}")
                return data
            except Exception as e:
                logger.debug(f"Modelo {model_name} falhou: {e}")
                continue
        
        return None
        
    except Exception as e:
        logger.error(f"Falha geral na análise com Gemini: {e}")
        return None
