# NexVents — Campus Event Management System

NexVents is a Flask + SQLite campus event portal. This release is based on the original **NexVents_Improved** project and keeps its existing visual design, home page, student/admin authentication, event board, QR tickets, QR check-in, and AI assistant structure.

## Included changes

1. **Coordinator portal** — Admin creates coordinator accounts; NexVents generates a unique Coordinator ID. Admin assigns coordinators to events. Coordinators sign in with their ID and password, see assigned events and teammates, and post event-wise team updates.
2. **Simple accommodation add-ons** — Admin can enable Stay and Food add-ons per event, set prices, and set stay capacity. Students can select add-ons during registration and see the calculated amount on their ticket. Admin can review add-on bookings.
3. **Medical and safety contacts** — Admin can add a first-aid/medical desk location and medical, security, and event emergency contact numbers for each event. These details appear on the event information page.
4. **Existing UI preserved** — The original NexVents_Improved styling and branding are kept. New coordinator/accommodation/safety screens use matching styles; no replacement theme, logo redesign, or new tagline is applied in this release.

## Requirements

- Python 3.10 or newer
- Windows, macOS, or Linux
- Internet access for the first dependency installation

## Windows setup (VS Code)

1. Extract the ZIP into a new folder.
2. Open the inner `NexVents` folder in VS Code (the folder containing `run.py` and `requirements.txt`).
3. Open Terminal → New Terminal.
4. Run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

5. Open http://127.0.0.1:5000 in your browser.

Alternatively, double-click `RUN_NEXVENTS.bat` from the project folder.

## Demo admin account

- Email: `admin@nexvents.demo`
- Password: `Admin@123`

Change this demo password before using the project outside a local demonstration.

## Coordinator workflow

1. Sign in as the demo admin.
2. Open **Coordinators** in the navigation or the Admin Dashboard.
3. Create a coordinator with their name, email, temporary password, and optional event assignment.
4. NexVents displays the generated ID in the admin success message and coordinator list. Share both the ID and temporary password with that person.
5. The coordinator opens **Coordinator login**, signs in, and uses the event-wise team update board.

## Accommodation workflow

1. Admin creates or edits an event.
2. Under **Accommodation add-ons**, enable Stay/Food and set prices and available stay places.
3. Student opens the event and selects Stay, Food, both, or neither during registration.
4. The chosen add-ons and calculated amount are saved with the registration and shown on the ticket.
5. Admin opens **Accommodation** to review participant selections.

**Payment note:** This release calculates and records the add-on amount only. It does not collect online payments. A payment gateway and merchant credentials are required for real payment collection.

## Medical and safety workflow

Admin enters the event's first-aid/medical desk location and contact numbers when creating/editing an event. Participants can view the details on the public event information page; phone numbers are clickable on supported devices.

## QR tickets and check-in

- Confirmed registrations receive a unique QR ticket.
- Admin opens **Check-in** to scan with the camera, upload a QR image, or enter a ticket code manually.
- The server validates confirmed tickets and prevents a ticket from being checked in twice.
- The QR image decoder uses `zxing-cpp`, included in `requirements.txt`.

## Database safety

The app applies additive SQLite migrations at startup. Existing users, events, registrations, ticket codes, and check-in timestamps are not intentionally deleted or recreated. Before replacing an existing local project, keep a backup of `instance/nexvents.sqlite`. The ZIP itself contains no user database; the app creates a new database on first run if none is present.

## Tests

To install the optional test runner and run the suite from the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The test suite covers core student registration, QR creation/check-in, waitlisting, accommodation add-ons, event safety information, coordinator creation/assignment/messaging, and migration of an older database.

## Notes

- Coordinator messages are an event-wise shared message board; this is not a real-time chat service.
- Stay/Food prices are estimates saved to the booking, not proof of payment.
- The project is intended for local development/demo use. Deploy behind a production WSGI server with a strong secret key before public hosting.
