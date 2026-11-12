# OpenF1 capability report

**Checked:** 12 November 2026, against openf1.org, its docs and authentication guide, and live calls to the API.

## Verified

| Question | Answer | How it was checked |
|---|---|---|
| Is historical data free? | Yes, from 2023 onwards, with no account. | Backfilled 2023–2026 without credentials. |
| Is live data free? | **No.** Data is "live" from 30 minutes before a session starts until 30 minutes after it ends, and live data needs the paid sponsor tier (€9.90/month, personal use). | openf1.org pricing and FAQ. |
| How is the paid tier used? | `POST https://api.openf1.org/token` with username and password returns an OAuth2 access token; requests then send `Authorization: Bearer <token>`. MQTT and WebSocket streaming are also offered. | openf1.org/auth.html. |
| Rate limits | Free: 3 requests/second and 30/minute. Paid: 6/second and 60/minute. | Site; the 30/minute limit was hit during backfill. |
| Stated delay | About 3 seconds behind live events. | Site FAQ (not measured). |
| Official results | `/v1/session_result` covers 2023–2025 races. | Loaded for all 70 races. |
| Starting grid | `/v1/starting_grid`, keyed by the qualifying session, available after qualifying. | Called for a 2025 weekend. |
| Position data | Timestamped events with no lap number. | Stored data and API responses. |
| Licence | Non-commercial, personal and educational use (CC BY-NC-SA 4.0). | Site footer and FAQ. |

## Not verified

- **Anything requiring a paid account.** No subscription was available, so the login flow, live responses and real latency are untested against the real service. The client's login code is covered only by a mocked test.
- **Behaviour of the free tier during a live session** (error code, empty response, or delayed data). The pipeline treats a failed or short response as "keep what is stored".
- **How soon after qualifying the starting grid appears**, and whether late penalties update it.

## What this means for the app

- **Without a subscription:** pre-race predictions work on race weekend (qualifying is free 30 minutes after it ends). In-race predictions cannot update during the race; the pipeline catches up once the race data becomes free, about 30 minutes after the flag.
- **With a subscription:** set `OPENF1_USERNAME` and `OPENF1_PASSWORD` in the environment before starting the pipeline. Nothing else changes. The credentials are never written to logs or files.
- Public hosting or redistribution would need permission beyond the personal-use licence.
