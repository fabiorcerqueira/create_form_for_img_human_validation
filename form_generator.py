"""Generate Google Forms in which human raters label the facial expression in images.

Each image is shown once, followed by a required single-choice question
("Qual emoção a pessoa da imagem acima está expressando?") with eight options:
Dor, Raiva, Nojo, Felicidade, Tristeza, Surpresa, Neutro and "Não é possível
determinar pela imagem". Images are split into consecutive forms of
--per-form images each (default 10), in ascending order of the number in the
file name (image_1.jpg, image_2.jpg, ...).

Part of the Florence project. See README.md for setup.

Examples
--------
Plan only, without contacting Google (no credentials needed):
    python form_generator.py --images imagens --dry-run

Create all forms:
    python form_generator.py --images imagens --credentials credenciais.json \
        --drive-folder <DRIVE_FOLDER_ID> --share-with you@example.org

Recreate only forms 15 and 16 (same image split as the full run):
    python form_generator.py ... --forms 15,16
"""
import argparse
import csv
import mimetypes
import os
import re
import sys
import time
from datetime import datetime

SCOPES = [
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/drive.file",
]
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jfif")

QUESTION = "Qual emoção a pessoa da imagem acima está expressando?"
OPTIONS = [
    "Dor",
    "Raiva",
    "Nojo",
    "Felicidade",
    "Tristeza",
    "Surpresa",
    "Neutro (a pessoa não esboça qualquer sentimento)",
    "Não é possível determinar pela imagem.",
]

# Text shown to raters in the original FPD-181 study (kept verbatim for reproducibility).
# Use --description-file to provide your own text.
DEFAULT_DESCRIPTION = """Olá,

Um trabalho científico que está sendo desenvolvido na UFF utiliza Inteligência Artificial para detectar o sentimento de uma pessoa numa imagem. O intuito é utilizar IA num processo contínuo de captura de imagens de uma câmera que monitore pessoas que necessitem de cuidados, como idosos, convalescentes (de pós-operatórios ou doenças), PCDs, enfim, de pessoas que precisem ser assistidas mas não podem ter um cuidador 24h por dia por perto. Nestas situações, se o sistema detectar o sentimento de dor, um alerta será automaticamente enviado para uma pessoa próxima, que não esteja presente no momento, que poderá visualizar as imagens e tomar uma decisão concernente a uma possível necessidade de ajuda.

Para tal, selecionamos algumas imagens para testar a IA. No entanto, precisamos rotular o sentimento de cada uma e, para isto, conto com sua preciosa ajuda. É só responder o formulário a seguir, que não leva mais que 3 minutos para ser concluído. Dedicando pouquíssimo tempo, você estará ajudando a nossa pesquisa e, potencialmente, um grande número de pessoas que irão se beneficiar do sistema criado.

Muito grato desde já."""

MANIFEST_FIELDS = [
    "form_number", "form_id", "edit_url", "responder_url",
    "question_position", "image_number", "file_name", "drive_file_id",
]


# --------------------------------------------------------------------------
# Image discovery and split into forms (pure functions, no Google access)
# --------------------------------------------------------------------------

def image_number(file_name):
    """Number used to identify an image: the last run of digits in the file stem."""
    stem = os.path.splitext(file_name)[0]
    matches = re.findall(r"\d+", stem)
    return int(matches[-1]) if matches else None


def list_images(folder):
    """Return [(number, file_name)] sorted by number; stop on missing or duplicate numbers."""
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTENSIONS))
    if not files:
        raise SystemExit(f"No images ({', '.join(IMAGE_EXTENSIONS)}) found in {folder!r}.")

    without_number = [f for f in files if image_number(f) is None]
    if without_number:
        raise SystemExit(
            "These files have no number in their name, so their answers could not be "
            "matched back to them: " + ", ".join(without_number)
        )

    by_number = {}
    for f in files:
        by_number.setdefault(image_number(f), []).append(f)
    duplicates = {n: fs for n, fs in by_number.items() if len(fs) > 1}
    if duplicates:
        detail = "; ".join(f"{n}: {', '.join(fs)}" for n, fs in sorted(duplicates.items()))
        raise SystemExit("Several files share the same image number: " + detail)

    return sorted((n, fs[0]) for n, fs in by_number.items())


def split_into_forms(images, per_form):
    """Consecutive chunks of `per_form` images; form numbers start at 1."""
    return {
        k // per_form + 1: images[k:k + per_form]
        for k in range(0, len(images), per_form)
    }


def parse_form_selection(text, available):
    if not text:
        return sorted(available)
    selected = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            selected.update(range(a, b + 1))
        elif part:
            selected.add(int(part))
    unknown = sorted(selected - set(available))
    if unknown:
        raise SystemExit(f"Form numbers not in the plan: {unknown} (plan has 1-{max(available)}).")
    return sorted(selected)


# --------------------------------------------------------------------------
# Google API
# --------------------------------------------------------------------------

def build_services(credentials_file):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return build("forms", "v1", credentials=creds), build("drive", "v3", credentials=creds)


def upload_image(drive, path, name, drive_folder):
    from googleapiclient.http import MediaFileUpload

    mimetype = mimetypes.guess_type(path)[0]
    if path.lower().endswith(".jfif") or not mimetype:
        mimetype = "image/jpeg"
    metadata = {"name": name}
    if drive_folder:
        metadata["parents"] = [drive_folder]
    uploaded = drive.files().create(
        body=metadata, media_body=MediaFileUpload(path, mimetype=mimetype), fields="id"
    ).execute()
    file_id = uploaded["id"]
    # Google Forms needs a publicly readable URL to fetch the image when the item is created.
    drive.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    return file_id


def make_private(drive, file_id):
    """Remove the 'anyone with the link' permission from an uploaded image."""
    perms = drive.permissions().list(fileId=file_id, fields="permissions(id,type)").execute()
    for p in perms.get("permissions", []):
        if p.get("type") == "anyone":
            drive.permissions().delete(fileId=file_id, permissionId=p["id"]).execute()


def create_form(forms, drive, args, form_number, images, description):
    """Create one form and return its manifest rows. Raises on any API error."""
    title = f"{args.title_prefix} #{form_number}"
    created = forms.forms().create(body={"info": {"title": title, "documentTitle": title}}).execute()
    form_id = created["formId"]
    edit_url = f"https://docs.google.com/forms/d/{form_id}/edit"
    responder_url = created.get("responderUri", "")

    forms.forms().batchUpdate(formId=form_id, body={"requests": [{
        "updateFormInfo": {"info": {"description": description}, "updateMask": "description"}
    }]}).execute()

    # The form ID is also its Drive file ID, so it can be shared directly.
    if args.share_with:
        drive.permissions().create(
            fileId=form_id,
            body={"type": "user", "role": "writer", "emailAddress": args.share_with},
            sendNotificationEmail=False,
        ).execute()

    requests, rows = [], []
    for position, (number, file_name) in enumerate(images):
        path = os.path.join(args.images, file_name)
        file_id = upload_image(drive, path, f"{form_number}_{file_name}", args.drive_folder)
        requests.append({"createItem": {
            "item": {"imageItem": {"image": {"sourceUri": f"https://drive.google.com/uc?id={file_id}"}}},
            "location": {"index": position * 2},
        }})
        requests.append({"createItem": {
            "item": {
                # The title carries the image number from the file name, so each answer
                # can be matched to its file even if the numbering has gaps.
                "title": f"Imagem {number}: {QUESTION}",
                "questionItem": {"question": {
                    "required": True,
                    "choiceQuestion": {
                        "type": "RADIO",
                        "options": [{"value": o} for o in OPTIONS],
                        "shuffle": False,
                    },
                }},
            },
            "location": {"index": position * 2 + 1},
        }})
        rows.append({
            "form_number": form_number, "form_id": form_id, "edit_url": edit_url,
            "responder_url": responder_url, "question_position": position + 1,
            "image_number": number, "file_name": file_name, "drive_file_id": file_id,
        })

    forms.forms().batchUpdate(formId=form_id, body={"requests": requests}).execute()

    if args.make_images_private:
        for row in rows:
            make_private(drive, row["drive_file_id"])
    return rows


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--images", default="imagens", help="folder with the images (default: imagens)")
    p.add_argument("--credentials", default="credenciais.json",
                   help="service-account JSON key (default: credenciais.json)")
    p.add_argument("--drive-folder", default=None,
                   help="ID of the Drive folder that receives the images; the folder must be "
                        "shared with the service account (default: the service account's own Drive)")
    p.add_argument("--share-with", default=None,
                   help="e-mail that receives edit access to each form (strongly recommended: "
                        "otherwise only the service account can open the forms)")
    p.add_argument("--per-form", type=int, default=10, help="images per form (default: 10)")
    p.add_argument("--title-prefix", default="Classificação de Emoções Faciais",
                   help="form title; the form number is appended")
    p.add_argument("--description-file", default=None,
                   help="UTF-8 text file with the form description (default: original study text)")
    p.add_argument("--forms", default=None,
                   help="create only these forms of the plan, e.g. '15' or '3,5-7'")
    p.add_argument("--make-images-private", action="store_true",
                   help="after each form is built, remove public link access from its images "
                        "(test with --forms 1 first: the form must still display them)")
    p.add_argument("--output-dir", default=".", help="where the output files are written")
    p.add_argument("--sleep", type=float, default=1.0, help="pause between forms, in seconds")
    p.add_argument("--dry-run", action="store_true",
                   help="print and save the plan without contacting Google")
    args = p.parse_args(argv)
    if args.per_form < 1:
        p.error("--per-form must be at least 1")
    return args


def main(argv=None, services=None):
    args = parse_args(argv)
    images = list_images(args.images)
    plan = split_into_forms(images, args.per_form)
    selected = parse_form_selection(args.forms, plan)

    description = DEFAULT_DESCRIPTION
    if args.description_file:
        with open(args.description_file, encoding="utf-8") as f:
            description = f.read().strip()

    os.makedirs(args.output_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix, k = "", 1
    while os.path.exists(os.path.join(args.output_dir, f"form_manifest_{stamp}{suffix}.csv")):
        k += 1
        suffix = f"_{k}"  # never overwrite the manifest of an earlier run
    manifest_path = os.path.join(args.output_dir, f"form_manifest_{stamp}{suffix}.csv")
    summary_path = os.path.join(args.output_dir, f"form_summary_{stamp}{suffix}.txt")

    print(f"{len(images)} images, {len(plan)} forms of up to {args.per_form} images; "
          f"creating {len(selected)} form(s).")
    numbers = [n for n, _ in images]
    gaps = sorted(set(range(numbers[0], numbers[-1] + 1)) - set(numbers))
    if gaps:
        print(f"Note: image numbers have gaps ({len(gaps)} missing between {numbers[0]} and "
              f"{numbers[-1]}); question titles use the file numbers, so answers still match.")

    if args.dry_run:
        with open(manifest_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            w.writeheader()
            for k in selected:
                for pos, (n, name) in enumerate(plan[k]):
                    w.writerow({"form_number": k, "question_position": pos + 1,
                                "image_number": n, "file_name": name})
                print(f"Form {k}: images {plan[k][0][0]}-{plan[k][-1][0]} ({len(plan[k])})")
        print(f"Dry run: plan saved to {manifest_path}. Nothing was sent to Google.")
        return 0

    if not args.share_with:
        print("WARNING: --share-with not given; only the service account will be able to "
              "open and edit the forms.", file=sys.stderr)

    forms, drive = services if services else build_services(args.credentials)

    created, failed = [], []
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for k in selected:
            try:
                rows = create_form(forms, drive, args, k, plan[k], description)
            except Exception as e:  # keep going; the failed form can be recreated with --forms
                print(f"ERROR in form {k}: {e}", file=sys.stderr)
                failed.append((k, str(e)))
            else:
                w.writerows(rows)
                f.flush()
                created.append(k)
                print(f"Form {k} created: {rows[0]['edit_url']}")
            time.sleep(args.sleep)

    used = {r_name for k in created for _, r_name in plan[k]}
    lines = [
        f"Images in folder: {len(images)}",
        f"Forms in plan: {len(plan)}; requested: {len(selected)}; "
        f"created: {len(created)}; failed: {len(failed)}",
        f"Images placed in a created form: {len(used)}",
    ]
    for k, err in failed:
        imgs = plan[k]
        lines.append(f"FAILED form {k} (images {imgs[0][0]}-{imgs[-1][0]}): {err}")
    if failed:
        lines.append("Recreate the failed forms with: --forms " + ",".join(str(k) for k, _ in failed))
    not_requested = [name for k in plan if k not in selected for _, name in plan[k]]
    if not_requested:
        lines.append(f"{len(not_requested)} images belong to forms not requested in this run.")
    summary = "\n".join(lines)
    print("\n" + summary)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
    print(f"\nManifest (form, question and image of each item): {manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
