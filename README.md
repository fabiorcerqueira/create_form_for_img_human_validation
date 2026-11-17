# Google Forms generator for human labeling of facial expressions

This is the Python script used to create the Google Forms in which human raters labeled the facial expression of the people in the images of the Florence project (FPD-181 image set). Other groups can use it to label their own image collections with the same procedure.

Each image is shown once, followed by a required single-choice question in Portuguese, *"Imagem N: Qual emoção a pessoa da imagem acima está expressando?"*, with eight options: Dor (pain), Raiva (anger), Nojo (disgust), Felicidade (happiness), Tristeza (sadness), Surpresa (surprise), Neutro (neutral) and "Não é possível determinar pela imagem" (cannot be determined from the image). Images are sorted by the number in their file name and split into consecutive forms of 10 images each (configurable).

## Setup

1. **Python 3.9+** and the dependencies: `pip install -r requirements.txt`.
2. **Google Cloud project:** in the Google Cloud Console, create (or choose) a project and enable the **Google Forms API** and the **Google Drive API**.
3. **Service account:** create a service account in that project, create a JSON key for it and save it next to the script as `credenciais.json` (or pass another path with `--credentials`). The key is a secret: it is listed in `.gitignore` and must never be committed.
4. **Drive folder (optional):** to keep the uploaded images in a folder of your own Drive, share that folder with the service account's e-mail address (found in the JSON key, field `client_email`) as *Editor*, and pass its ID (the last part of the folder URL) with `--drive-folder`.
5. **Images:** put the images (`.jpg`, `.jpeg`, `.png`, `.jfif`) in a folder, by default `imagens/`. Every file name must contain a number that identifies the image (e.g., `image_37.jpg`), and numbers must be unique. The number is shown in each question title, so every answer can be matched to its file.

## Usage

Check the plan first; this does not contact Google:

    python form_generator.py --images imagens --dry-run

Create the forms:

    python form_generator.py --images imagens --credentials credenciais.json \
        --drive-folder <DRIVE_FOLDER_ID> --share-with you@example.org

`--share-with` gives your Google account edit access to each form. Without it, the forms belong only to the service account and you cannot open them.

Main options (see `python form_generator.py --help`):

| Option | Default | Meaning |
|---|---|---|
| `--images` | `imagens` | folder with the images |
| `--per-form` | `10` | images per form |
| `--title-prefix` | `Classificação de Emoções Faciais` | form title; ` #<form number>` is appended |
| `--description-file` | original study text | UTF-8 file with the description shown to raters |
| `--forms` | all | create only some forms of the plan, e.g. `15` or `3,5-7` |
| `--make-images-private` | off | remove public link access from the images after each form is built |
| `--output-dir` | `.` | where the manifest and summary are written |

## Outputs

- `form_manifest_<date>.csv`: one row per question, with the form number, form ID, edit and responder URLs, question position, image number, file name and Drive file ID. Keep it: it is the link between the answers and the image files.
- `form_summary_<date>.txt`: images found, forms created and forms that failed.

If a form fails, the others are still created and the summary prints the command to recreate only the failed forms (`--forms ...`); the image split does not change between runs. A form that failed halfway may remain in the service account's Drive and can be deleted.

Responses are collected by Google Forms; export them from each form (Responses > Download CSV) and use the question titles ("Imagem N") or the manifest to associate each answer with its image.

## Privacy

Google Forms fetches each image from a publicly readable Drive link when the question is created, so the script makes each uploaded image readable by anyone with the link. This was acceptable for FPD-181, which uses stock photographs from Pexels, Unsplash and Pixabay (licenses allowed for free and editorial use).

## Notes on the FPD-181 study

The original study used the default settings: 199 images in 20 forms (19 with 10 images and one with 9). The description shown to raters is kept verbatim in the script (`DEFAULT_DESCRIPTION`) for reproducibility; it says that the alerted person may view the images, whereas the current Florence e-mail alert contains text only.

Part of the Florence project:
- Florence application: https://github.com/fabiorcerqueira/Florence
- Images and human assessments: https://github.com/fabiorcerqueira/pain_nopain_img_rep
