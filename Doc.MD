hisaabkitaab/
├── .env                    (you create locally — real secrets, git-ignored)
├── .env.example            (template — no real secrets, safe to commit)
├── .gitignore              (keeps .env, __pycache__, venv/ out of git)
├── requirements.txt
├── create_tables.py        ← Phase 4a: one-off script to sync missing tables to Postgres
└── app/
    ├── __init__.py
    ├── config.py            (Settings — reads .env, single source of truth)
    ├── database.py          (async engine/session; NOTE: `Base` lives here in your local project)
    ├── models.py            (all SQLAlchemy ORM tables)
    ├── schemas.py            (all Pydantic v2 request/response shapes)
    ├── auth.py               (bcrypt hashing, JWT issue/verify, get_current_user/require_role)
    ├── bill_generator.py    ← NEW: builds the PDF bill from a Transaction
    ├── main.py               (FastAPI app + router mounting + /login, /me)
    └── routers/
        ├── __init__.py
        ├── parties.py        (CRUD + payments + statement)
        ├── items.py          (CRUD + stock adjustment)
        └── transactions.py   (POS sale flow + bill download)






config.py
    ↑
database.py  (Base + engine/session)
    ↑
models.py  (Tenant, User, Party, Item, Transaction, TransactionItem, PartyPayment)
    ↑
schemas.py  (Pydantic wire shapes)
    ↑
auth.py  (JWT + bcrypt)
    ↑
bill_generator.py  (pure function: Transaction + Tenant + Party → PDF bytes;
                     depends only on models.py, not on FastAPI/routing at all)
    ↑
routers/*.py  (depend on auth.py, database.py, models.py, schemas.py,
               and transactions.py also imports bill_generator.py)
    ↑
main.py  (mounts the routers)



<!-- FRONTEND -->

~/Desktop/HisaabKitaab/            ← just a folder on disk, NOT itself a git repo
├── env/                            (your Python venv — stays exactly where it is)
├── backend/                        ← git repo #1 (already exists — you're on branch "main")
│   ├── .git/
│   ├── .env                        (real secrets — gitignored)
│   ├── .env.example
│   ├── .gitignore
│   ├── requirements.txt
│   ├── seed.py
│   ├── create_tables.py
│   ├── Doc.MD
│   └── app/
│       ├── config.py
│       ├── database.py
│       ├── models.py
│       ├── schemas.py
│       ├── auth.py
│       ├── bill_generator.py
│       ├── main.py
│       └── routers/
│
└── frontend/                       ← git repo #2 (new)
    ├── .git/
    ├── .env                        (real — gitignored)
    ├── .env.example
    ├── .gitignore
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── index.html
    └── src/
        ├── main.js
        ├── App.vue
        ├── style.css
        ├── api/client.js
        ├── stores/auth.js
        ├── router/index.js
        ├── layouts/AppShell.vue
        └── views/




<!-- FE file structure -->

frontend/
├── .env.example
├── .gitignore
├── index.html
├── package.json
├── postcss.config.js
├── tailwind.config.js
├── vite.config.js
└── src/
    ├── App.vue
    ├── main.js
    ├── style.css
    ├── api/client.js
    ├── layouts/AppShell.vue
    ├── router/index.js
    ├── stores/auth.js
    └── views/
        ├── LoginView.vue
        └── DashboardView.vue