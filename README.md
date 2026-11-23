# FrameFlow – Smart Financial Co-Pilot

**Capture the moment. Stay ahead of your money.**

FrameFlow is a Django-based financial management platform designed to help independent photographers and small businesses manage clients, projects, invoices, payments, expenses, and cash-flow forecasts in one place.

## Features

- **Business Management:** Manage business profiles and business types.
- **Client Management:** Create and view client records.
- **Project Management:** Organize projects and connect them with clients.
- **Invoice Management:** Create, issue, and track invoices.
- **Payment Tracking:** Record payments and monitor outstanding balances.
- **Expense Management:** Record and manage business expenses.
- **Cash-Flow Forecasting:** View projected income, expenses, and closing balances.
- **What-If Analysis:** Explore alternative financial scenarios.

## Technology Stack

- Python 3.12
- Django 5.2
- MySQL
- Bootstrap 5
- HTML, CSS, JavaScript
- Chart.js

## Project Structure

```text
FrameFlow/
├── apps/
│   ├── clients/
│   ├── core/
│   ├── finance/
│   └── forecasting/
├── config/
├── static/
├── manage.py
├── requirements.txt
└── README.md
```

## Installation and Setup

### 1. Clone the repository

```bash
git clone YOUR_REPOSITORY_URL
cd FrameFlow
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

**Windows PowerShell**
```powershell
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root and configure the database settings required by the application.

Do not commit database passwords, secret keys, or other credentials.

### 5. Apply migrations

After configuring the database, run:

```bash
python manage.py migrate
```

### 6. Start the development server

```bash
python manage.py runserver
```

Open `http://127.0.0.1:8000/` in your browser.

## Running Tests

Run the automated test suite with:

```bash
python manage.py test --settings=config.settings_test
```

## Security Notes

- Keep secret keys and database credentials in environment variables.
- Do not commit `.env` files.
- Use the production database configuration for deployment.
- Review permissions and business-level data access before deployment.

## Project Purpose

FrameFlow aims to make financial management easier for independent professionals and small businesses by connecting everyday business activities with clear cash-flow insights.

---

**FrameFlow – Capture the moment. Stay ahead of your money.**
