"""Publica el dashboard en un Space de Hugging Face (SDK Docker).

Uso: python deploy/huggingface/publish.py <usuario>/<space>
Token: variable HF_TOKEN o sesión de `hf auth login`.
"""

import argparse
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
# Lista explícita: solo sube lo que necesita la imagen. Nada de .env, .venv ni tests.
ALLOW = [
    "Dockerfile",
    ".dockerignore",
    "requirements.txt",
    "src/**",
    "app/**",
    "data/**",
    "models/**",
    ".streamlit/**",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("space_id", help="<usuario>/<space>")
    args = parser.parse_args()

    api = HfApi()
    api.create_repo(args.space_id, repo_type="space", space_sdk="docker", exist_ok=True)
    # La tarjeta del Space va primero: su cabecera YAML le dice a HF que es Docker y en qué puerto escucha.
    api.upload_file(
        path_or_fileobj=ROOT / "deploy" / "huggingface" / "README.md",
        path_in_repo="README.md",
        repo_id=args.space_id,
        repo_type="space",
        commit_message="Tarjeta del Space",
    )
    api.upload_folder(
        folder_path=ROOT,
        repo_id=args.space_id,
        repo_type="space",
        allow_patterns=ALLOW,
        ignore_patterns=["**/__pycache__/**", "*.pyc"],
        delete_patterns=ALLOW,  # borra del Space lo que ya no exista en el repo
        commit_message="Despliegue desde GitHub",
    )
    print(f"https://huggingface.co/spaces/{args.space_id}")


if __name__ == "__main__":
    main()
