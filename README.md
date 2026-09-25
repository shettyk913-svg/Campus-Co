# CampuSCO

Find, book and coordinate underused campus resources. Built for the Error Squad hackathon idea.

**Key feature: automatic slot release.** If a student doesn't check in within the grace period, the booking becomes `NO_SHOW`, the slot is freed, and the first student on the waiting list is offered it. A background job checks every 15 seconds, and every API call also checks.

## Run

Needs Python 3.9+.

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. The SQLite database (`backend/campusco.db`) is created and seeded on first run.

## Demo

Go to **Admin & demo → Run no-show demo**. It uses a simulated clock: Pavan books, Krithi joins the waitlist, Pavan doesn't show, the slot is auto-released, Krithi accepts, checks in and completes.

## What's inside

- `backend/app.py`: Flask REST API, SQLite, auto-release job
- `frontend/index.html`: HTML/CSS/JS single page app (served by Flask)
- Features: resource discovery, eligibility checks, resource manager/permission details, student skills, bookings, waiting list with promotion, QR check-in (simulated), cancel/reschedule, notifications, Faculty Connect, enhanced rule-based AI assistant, approval workflow, admin dashboard

## Notes

- The AI assistant is rule-based in this prototype. It now uses resource metadata, eligibility, manager information and student-skill matching. Replace the `/api/ai` handler with a Gemini/OpenAI call to make it fully AI-powered.
- QR check-in is simulated with a button. Swap in a QR library and scanner for real use.
- There is no login yet. Use the "Viewing as" dropdown to switch users.
