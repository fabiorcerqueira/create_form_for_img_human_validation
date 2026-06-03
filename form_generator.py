import os
import re
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/forms.body', 'https://www.googleapis.com/auth/drive.file']
SERVICE_ACCOUNT_FILE = 'credenciais.json'
IMAGE_FOLDER = 'imagens'
DRIVE_FOLDER_ID = '17oTCr57ziTwLn6IHLZZmxplElanTS7aU'
YOUR_EMAIL = ''

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
forms_service = build('forms', 'v1', credentials=creds)
drive_service = build('drive', 'v3', credentials=creds)

def extrair_numero(nome_arquivo):
    match = re.search(r'(\d+)', nome_arquivo)
    return int(match.group(1)) if match else 0

todas_imagens = [f for f in os.listdir(IMAGE_FOLDER) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.jfif'))]
todas_imagens.sort(key=extrair_numero)
todas_imagens_utilizadas = set()

total_imagens_necessarias = 199
if len(todas_imagens) < total_imagens_necessarias:
    print(f"⚠️ Imagens insuficientes! Precisa de {total_imagens_necessarias}, tem apenas {len(todas_imagens)}")
    exit()

indice_imagem = 0

descricao_formulario = """Olá,

Um trabalho científico que está sendo desenvolvido na UFF utiliza Inteligência Artificial para detectar o sentimento de uma pessoa numa imagem. O intuito é utilizar IA num processo contínuo de captura de imagens de uma câmera que monitore pessoas que necessitem de cuidados, como idosos, convalescentes (de pós-operatórios ou doenças), PCDs, enfim, de pessoas que precisem ser assistidas mas não podem ter um cuidador 24h por dia por perto. Nestas situações, se o sistema detectar o sentimento de dor, um alerta será automaticamente enviado para uma pessoa próxima, que não esteja presente no momento, que poderá visualizar as imagens e tomar uma decisão concernente a uma possível necessidade de ajuda.

Para tal, selecionamos algumas imagens para testar a IA. No entanto, precisamos rotular o sentimento de cada uma e, para isto, conto com sua preciosa ajuda. É só responder o formulário a seguir, que não leva mais que 3 minutos para ser concluído. Dedicando pouquíssimo tempo, você estará ajudando a nossa pesquisa e, potencialmente, um grande número de pessoas que irão se beneficiar do sistema criado.

Muito grato desde já."""

for i in range(1, 21):
    try:
        num_perguntas = 10 if i < 20 else 9

        selected_images = todas_imagens[indice_imagem:indice_imagem+num_perguntas]
        indice_imagem += num_perguntas
        todas_imagens_utilizadas.update(selected_images)

        form_title = f"Classificação de Emoções Faciais #{i}"
        new_form = {
            "info": {
                "title": form_title,
                "documentTitle": form_title
            }
        }
        created_form = forms_service.forms().create(body=new_form).execute()
        form_id = created_form["formId"]

        # === Adicionar descrição após criar o formulário ===
        forms_service.forms().batchUpdate(formId=form_id, body={
            "requests": [
                {
                    "updateFormInfo": {
                        "info": {
                            "description": descricao_formulario
                        },
                        "updateMask": "description"
                    }
                }
            ]
        }).execute()

        form_drive_file = drive_service.files().list(
            q=f"name = '{form_title}' and mimeType = 'application/vnd.google-apps.form'",
            fields="files(id, name)",
            spaces='drive'
        ).execute()
        form_file_id = form_drive_file['files'][0]['id']

        drive_service.permissions().create(
            fileId=form_file_id,
            body={
                'type': 'user',
                'role': 'writer',
                'emailAddress': YOUR_EMAIL
            },
            sendNotificationEmail=False
        ).execute()

        requests = []
        for index, image_name in enumerate(selected_images):
            image_path = os.path.join(IMAGE_FOLDER, image_name)

            file_metadata = {
                'name': f"{i}_{image_name}",
                'parents': [DRIVE_FOLDER_ID]
            }
            media = MediaFileUpload(image_path, mimetype='image/jpeg')
            uploaded_file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            ).execute()
            file_id = uploaded_file['id']

            drive_service.permissions().create(
                fileId=file_id,
                body={'type': 'anyone', 'role': 'reader'},
            ).execute()
            image_url = f"https://drive.google.com/uc?id={file_id}"

            requests.append({
                "createItem": {
                    "item": {
                        "imageItem": {
                            "image": {
                                "sourceUri": image_url
                            }
                        }
                    },
                    "location": {
                        "index": index * 2
                    }
                }
            })

            numero_imagem_global = indice_imagem - num_perguntas + index + 1
            requests.append({
                "createItem": {
                    "item": {
                        "title": f"Imagem {numero_imagem_global}: Qual emoção a pessoa da imagem acima está expressando?",
                        "questionItem": {
                            "question": {
                                "required": True,
                                "choiceQuestion": {
                                    "type": "RADIO",
                                    "options": [
                                        {"value": "Dor"},
                                        {"value": "Raiva"},
                                        {"value": "Nojo"},
                                        {"value": "Felicidade"},
                                        {"value": "Tristeza"},
                                        {"value": "Surpresa"},
                                        {"value": "Neutro (a pessoa não esboça qualquer sentimento)"},
                                        {"value": "Não é possível determinar pela imagem."}
                                    ],
                                    "shuffle": False
                                }
                            }
                        }
                    },
                    "location": {
                        "index": index * 2 + 1
                    }
                }
            })

        forms_service.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()

        form_link = f"https://docs.google.com/forms/d/{form_id}/edit"
        print(f"✅ Formulário {i} criado: {form_link}")
        with open("form_links.txt", "a") as f:
            f.write(f"{form_link}\n")

        time.sleep(1)

    except Exception as e:
        print(f"❌ Erro ao criar formulário {i}: {e}")
        with open("form_erros.txt", "a") as log:
            log.write(f"Erro no formulário {i}: {e}\n")
        continue

# === RESUMO DE USO DE IMAGENS ===
nao_utilizadas = sorted(set(todas_imagens) - todas_imagens_utilizadas)

resumo = []
resumo.append("\n🧾 Resumo de uso de imagens:")
resumo.append(f"Total de imagens na pasta: {len(todas_imagens)}")
resumo.append(f"Total de imagens utilizadas: {len(todas_imagens_utilizadas)}\n")

if nao_utilizadas:
    resumo.append(f"⚠️ {len(nao_utilizadas)} imagens **não foram utilizadas** em nenhum formulário:")
    for img in nao_utilizadas:
        resumo.append(f" - {img}")
else:
    resumo.append("✅ Todas as imagens foram utilizadas pelo menos uma vez.")

resumo_final = "\n".join(resumo)
print(resumo_final)

with open("resumo_imagens.txt", "w") as f:
    f.write(resumo_final)
