"""Print missing Phase 18 deployment variable names without exposing values."""

import os

from dotenv import dotenv_values

REQUIRED = (
    "METADATA_DATABASE_URL",
    "BLOB_READ_WRITE_TOKEN",
    "PINECONE_API_KEY",
    "PINECONE_INDEX_NAME",
    "PINECONE_INDEX_HOST",
    "OPENAI_API_KEY",
    "JWT_SECRET",
    "WORKFLOW_SECRET",
    "WORKFLOW_DISPATCH_URL",
    "FRONTEND_URL",
    "AUTH_ALLOWED_ORIGINS",
    "AUTH_EMAIL_WEBHOOK_URL",
    "AUTH_EMAIL_WEBHOOK_SECRET",
)


def main() -> None:
    values = {**dotenv_values(".env"), **os.environ}
    missing = [name for name in REQUIRED if not values.get(name)]
    print("Missing deployment configuration:")
    for name in missing:
        print(f"- {name}")
    if not missing:
        print(
            "All required variable names are populated; verify values with deployment smoke tests."
        )


if __name__ == "__main__":
    main()
