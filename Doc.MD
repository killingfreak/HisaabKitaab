hisaabkitaab/
├── .env                    (you create this locally — real secrets, git-ignored)
├── .env.example            (template committed to git — no real secrets)
├── .gitignore              (keeps .env, __pycache__, venv/ out of git)
├── requirements.txt
└── app/
    ├── __init__.py
    ├── config.py            ← NEW
    ├── database.py
    ├── models.py
    ├── schemas.py
    ├── auth.py
    ├── main.py
    └── routers/
        ├── __init__.py
        ├── parties.py
        ├── items.py
        └── transactions.py