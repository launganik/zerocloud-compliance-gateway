# ZeroCloud Compliance Gateway

Step 0 before ELM: an on-prem gateway that screens inbound booking emails with deterministic rules and a small local language model running on a Raspberry Pi 5. No data leaves the box. Clean bookings go on to ELM as structured data; sensitive ones (dual-use goods, technical data, confidential contracts, ID numbers) are held for compliance review.

Hackathon prototype. The control list, samples and knowledge base are illustrative, not official Forto policy.

## Run

Needs Python 3 (standard library only) and [Ollama](https://ollama.com) with a model pulled, e.g. `ollama pull llama3.2:3b`.

```bash
ZC_MODEL=llama3.2:3b python3 app.py
```

Open http://localhost:8080.

## Layout

| Path | What it is |
|---|---|
| `app.py` | Gateway backend: rules, local AI gate, booking extraction, knowledge-base Q&A, audit log |
| `static/index.html` | Web UI: Gate and Compliance review tabs |
| `rules/control_list.json` | Demo control list (HS prefixes, keywords, patterns) |
| `knowledge/` | Demo knowledge base used by "Ask Forto's knowledge base" |
| `samples/` | Three fictional booking emails |
| `setup/` | Raspberry Pi systemd service and offline Wi-Fi hotspot scripts |
| `GUIDE.md` / `GUIDE.html` | Step-by-step build guide and 3-minute demo script |
