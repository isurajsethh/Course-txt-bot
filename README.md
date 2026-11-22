# Sanskari Course Extractor & Delivery Bot

Features:
- Platform selection (`Dishaonlineclasses` with `https://dishaonlineclassesapi.classx.co.in`, `Target Board`, `PW`, `RWA`, `Custom API`)
- `ID*Password` aur step-by-step login
- Course catalog loading & `.txt` course export
- Target Channel & Group Topic (`message_thread_id`) delivery with live status and `/stop`
- Configurable inclusion/exclusion of `sealed.invalid` / masked links in `.txt` export

Required Render Env Var:
- `BOT_TOKEN`

Optional Render Env Vars:
- `ADMIN_IDS`
- `DEFAULT_CREDIT`
- `PORT`
