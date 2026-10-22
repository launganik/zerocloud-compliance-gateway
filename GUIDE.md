# ZeroCloud AI: 5-hour build guide

**AI that never talks to a datacenter.** A small language model runs on a Raspberry Pi 5 as a
**compliance gateway: step 0 before ELM.** Every inbound booking email is screened on our own hardware first.
Ordinary bookings go on to ELM; anything with dual-use goods, technical data or confidential material is held on-prem
until a compliance manager approves it. Sanctions screening stays in TMS, which already covers it.

You will end with this setup on stage:

```
MacBook (browser only)                         Raspberry Pi 5 (8 GB) = the whole "datacenter"
http://10.42.0.1:8080  ──Wi-Fi "ZeroCloud"──▶  ├─ ZeroCloud app  port 8080   gateway + compliance review
                         (broadcast by the Pi,  └─ Ollama          port 11434  llama3.2:3b model, kept in RAM
                          no internet uplink)
```

**The one insight:** *some booking emails must not reach cloud AI, and you only know which ones after reading them.
So the reading has to happen on our own box. ZeroCloud is that step zero before ELM.*

The demo story: *a booking email arrives → rules screen it in a millisecond → the local AI reads the unclear ones →
clean bookings go to ELM, sensitive ones are held on the box → a compliance manager approves or rejects → every decision is logged.*

```
Inbound booking email
        │
        ▼
┌──────────── STEP 0: ZeroCloud compliance gateway (own server) ────────────┐
│ 1. Fast rules (every email): control-list HS codes, military/dual-use     │
│    wording, technical specs, confidential contract terms, ID numbers      │
│ 2. Local language model (only emails the rules can't decide)              │
│ 3. Decision, fail-closed:                                                 │
│      CLEAR ─────────────────────────────────────────▶ ELM (normal flow)   │
│      HOLD ──▶ on-prem review queue ──▶ compliance manager                 │
│                                         ├─ Approve ──▶ ELM (+ audit log)  │
│                                         └─ Reject  ──▶ customer follow-up │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 0: The basics (read once, 10 minutes)

| Term | What it means | Why it matters here |
|---|---|---|
| **SLM (small language model)** | A language model with roughly 0.5 to 4 billion "parameters" (learned numbers), versus hundreds of billions for cloud models. | Small enough to fit in the Pi's 8 GB RAM and run on its CPU. |
| **Inference** | *Using* a trained model to produce answers. Training it is a separate, far bigger job. | We only do inference. Training needs datacenter GPUs; running a model does not. |
| **Quantization** | Storing each parameter in ~4 bits instead of 16. A 3B model shrinks from ~6 GB to ~2 GB. | Makes it fit in RAM and run faster, with a small loss in quality. Ollama's models come pre-quantized. |
| **Token** | A chunk of text the model reads or writes, roughly ¾ of a word. | Speed is measured in tokens per second. Expect ~5–7 tok/s for a 3B model on a Pi 5. |
| **Ollama** | A free program that downloads models and runs them, with a local web API on port 11434. | Handles all the hard parts (llama.cpp, ARM optimizations) with one install command. |
| **Headless** | Running a computer with no screen or keyboard attached. | You only have the MacBook, so you control the Pi from the Mac's Terminal. |
| **SSH** | "Secure shell": typing commands on the Pi from the Mac's Terminal over the network. | Your only way into a headless Pi. |
| **`zerocloud.local`** | mDNS: the Pi announces its name on the local network so you don't need its IP address. | Lets you type `ssh zero@zerocloud.local` instead of hunting for an IP. |
| **systemd service** | Linux's way to start a program automatically at boot and restart it if it crashes. | On stage you just plug in the power. Ollama installs itself as one; we add one for the app. |
| **Hotspot (access point)** | The Pi creates its own Wi-Fi network instead of joining one. | The off-grid proof: the Mac connects only to the Pi, and nothing connects to the internet. |

**Why the Pi 5 can do this:** its four Cortex-A76 cores (2.4 GHz) support the ARM vector
instructions (NEON, dot-product) that make model math fast on a CPU, and the 8 GB of LPDDR4X RAM
holds a 2 GB model with room to spare. It has no GPU that helps, so everything runs on the CPU,
and that is fine for a small model.

**Why "AI reads, the lists decide":** a 3B model is good at reading messy human text but can make
mistakes, and no model can promise "zero hallucination". So the design makes a mistake harmless instead:
- The model only **reads and flags**. It can never clear a shipment on its own.
- Every finding points to the sentence it came from. A quote or HS code that isn't in the email is marked
  "not found" and never used.
- HS codes are checked against a **control list file** by plain code, as Compliance asked
  ("an AI parser must link to official, deterministic control lists").
- **Fail-closed:** an uncertain answer, a missing HS code or a model error means HOLD, never CLEAR.
  The worst case is a false alarm a human checks.

---

## Your 5-hour timeline

| Time | Phase | Done when |
|---|---|---|
| 0:00–0:20 | 1. Assemble the Pi, install Imager on the Mac | Cooler attached, Imager open |
| 0:20–0:50 | 2. Flash the SD card and boot | `ssh zero@zerocloud.local` works |
| 0:50–1:15 | 3. Update and health-check the Pi | `free -h` shows ~8 GB, no throttling |
| 1:15–2:00 | 4. Install Ollama and models, first chat | You chatted with the model, noted tok/s |
| 2:00–2:45 | 5. Deploy and test the ZeroCloud gateway | All 4 sample emails give the expected decision |
| 2:45–3:30 | 6. Go off-grid: autostart + hotspot | Cold boot → Mac on "ZeroCloud" Wi-Fi → app works |
| 3:30–4:30 | 7. Slides, rehearsal, backup video | 3 timed run-throughs under 3:00 |
| 4:30–5:00 | Buffer | Something always breaks. This is for that. |

If you fall behind, cut from Phase 7 (fewer rehearsals), never from Phase 6: the cold-boot test is what makes the live demo safe.

---

## Phase 1: Assemble (0:00–0:20)

**On the Pi (hardware):**

- [ ] **Attach the cooler or case fan from the kit.** If it's the official Active Cooler, peel the thermal-pad film,
      push the two spring pins into the holes beside the CPU, and plug its cable into the **FAN** connector.
      **Why:** running a language model keeps all 4 cores at 100%. Without cooling the Pi passes 80 °C,
      slows itself down ("throttling"), and your demo gets slower mid-talk.
- [ ] **Find the microSD card in the kit** (a fingernail-sized card, often packed with a larger SD-size adapter).
      If it's already inside the Pi's card slot, pull it out. The slot is on the **underside** of the board,
      at the short edge opposite the USB ports, and the card just slides out (there's no click mechanism).
      **Why:** the microSD card is the Pi's hard drive. The operating system, Ollama and the model all live on it,
      and the Pi has no built-in storage. Even if your card came with an OS pre-installed, you'll overwrite it in
      Phase 2, because the Pi needs your Wi-Fi details and SSH switched on *before* its first boot.
- [ ] Put the board in the case. Note where the case's opening for the card slot is, because you'll insert the card through it later.
- [ ] **Work out how the card will get into your MacBook:**
  - **MacBook Pro 14" or 16" (2021 or later)** has an SD card slot on the right side. Put the microSD into the full-size SD adapter and use that slot.
  - **MacBook Air, or an older 13" MacBook Pro,** has no card slot, so you need a **USB-C card reader**. Check the kit, since some Berrybase kits include one.
    If you don't have one, ask the hackathon organisers **now**.
  **Why this is a blocker:** the Mac has to write the operating system onto the card. With no screen or keyboard on the Pi, there's no other way to install it.
- [ ] Have the **USB-C power supply from the kit** (27 W, 5 V / 5 A) ready, but **don't plug it in yet.**
      **Why this supply:** a MacBook charger or phone charger often can't deliver 5 A at 5 V. The Pi then
      throttles or warns about under-voltage under AI load.

> **End of Phase 1: what you should have**
> - The Pi in its case with the cooler attached, **no card inside, power not connected.**
> - The microSD card (in its adapter or card reader) next to your MacBook.
> - The power supply unplugged.
>
> **Why not power on yet?** With no card, or a card without your settings, the Pi has nothing useful to start,
> and you couldn't reach it from the Mac anyway. The very first power-on happens at the end of Phase 2, once the
> card holds the OS plus your Wi-Fi and SSH settings. That first boot is when the Pi applies them.

**On the MacBook:**

- [ ] Install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/> (or `brew install --cask raspberry-pi-imager` if you use Homebrew).
      **Why:** it downloads the operating system and writes it to the microSD card. It can also pre-configure
      Wi-Fi, username and SSH, which is what makes a headless setup possible.
- [ ] Decide which Wi-Fi the Pi joins for setup (it needs internet once, to download Ollama and the models, ~4 GB in total):
  - **Venue Wi-Fi**, only if it's plain WPA2 with a password. **Not** one with a login web page (captive portal): the Pi can't click through it.
  - **Your phone's hotspot** is the safe fallback. Both the Mac and the Pi must be on the **same** network.

---

## Phase 2: Flash the SD card and boot (0:20–0:50)

**On the MacBook:**

- [ ] Connect the microSD card to the Mac:
  - **SD slot:** slide the microSD into the SD adapter. If the adapter has a small **lock switch** on its side, push it toward the gold contacts (unlocked).
    A locked adapter makes the write fail. Insert the adapter into the Mac's slot, label up, contacts first.
  - **USB-C card reader:** put the microSD in the reader's micro slot and plug the reader into the Mac.
  - macOS may show the card on the desktop or in Finder, or show nothing at all. Both are fine.
    If macOS asks whether Raspberry Pi Imager may access **removable volumes**, click **Allow**.
- [ ] Open Raspberry Pi Imager and choose:
  - **Device:** Raspberry Pi 5
  - **OS:** *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**
    **Why Lite:** no desktop, because you have no screen. That leaves more RAM and CPU for the model, and it boots faster.
    **Why 64-bit:** Ollama only supports 64-bit ARM.
  - **Storage:** your microSD card (check the size so you don't wipe a different drive)
- [ ] In the **customisation / OS settings** step, set:
  - **Hostname:** `zerocloud`
  - **Username:** `zero` and a password you'll remember (this guide uses `zero` everywhere)
  - **Wi-Fi:** the SSID and password of your setup network; **Wi-Fi country: DE** (or wherever you are)
    **Why the country:** Wi-Fi channels are legally regulated per country. Without it, the Pi's Wi-Fi stays off.
  - **Locale/time zone:** Europe/Berlin, keyboard as you like
  - **Services / Remote access:** **Enable SSH** with password authentication
    **Why:** SSH is your only way in, and it's off by default for security.
- [ ] Click **Write** and confirm that the card will be erased. Writing plus verifying takes ~5–10 minutes. Enter your Mac password if asked.
- [ ] When Imager says it's finished, it ejects the card for you. If macOS then shows **"The disk you inserted was not readable"**,
      click **Eject** (never *Initialize*: that would erase what you just wrote).
      **Why the warning:** part of the card is now in Linux's format (ext4), which macOS can't read. That's expected.
- [ ] Take the microSD out of the adapter or reader.

**On the Pi (hardware), the first power-on:**

- [ ] Slide the microSD into the slot on the **underside** of the Pi, through the opening in the case: label facing away from the board,
      gold contacts facing the board. Push gently until it stops. A millimetre or two sticking out is normal.
- [ ] Plug the kit's USB-C power supply into the Pi's **USB-C port** (labelled *PWR IN*), then into the wall.
      The Pi 5 **turns on by itself** when power arrives, so you don't need to press its small power button.
- [ ] Watch the LED next to the power button: it should flicker **green** (the Pi is reading the card). If it stays **red only**,
      the card isn't inserted properly or wasn't written correctly.
- [ ] **Wait 2–3 minutes.** The first boot resizes the file system and applies your settings, and may reboot once by itself.

**On the MacBook (Terminal app, ⌘+Space → "Terminal"):**

- [ ] Connect to the Pi:
  ```bash
  ssh zero@zerocloud.local
  ```
  Type `yes` to trust the fingerprint, then your password (nothing appears as you type; that's normal).
  If macOS asks to allow Terminal to "find devices on your local network", click **Allow**.
  **Why:** you're now typing commands *on the Pi*. Your prompt changes to `zero@zerocloud:~ $`.
- [ ] If `zerocloud.local` isn't found after 5 minutes, see [Troubleshooting](#troubleshooting).

From here on, **"On the Pi"** means: type it in the SSH session. **"On the Mac"** means: a *new* Terminal window (⌘+N) that is *not* connected to the Pi.

---

## Phase 3: Update and health-check (0:50–1:15)

**On the Pi:**

- [ ] Update all software (5–15 minutes):
  ```bash
  sudo apt update && sudo apt full-upgrade -y
  ```
  **Why:** gets security fixes and the latest Pi firmware. `sudo` = "run as administrator";
  `apt` = the Pi's app store for the command line.
- [ ] Confirm the hardware:
  ```bash
  cat /proc/device-tree/model; echo
  free -h
  df -h /
  vcgencmd measure_temp
  vcgencmd get_throttled
  ```
  **Expect:** "Raspberry Pi 5", ~7.9 GB total memory, at least 10 GB free disk, temperature under 60 °C,
  and `throttled=0x0`.
  **Why:** `0x0` means the power supply and cooling are fine. Anything else means a weak power supply or overheating, so fix that now, not on stage.
- [ ] Reboot to apply the updates, then reconnect:
  ```bash
  sudo reboot
  ```
  Wait ~45 seconds, then on the Mac: `ssh zero@zerocloud.local`

---

## Phase 4: Install Ollama and the models (1:15–2:00)

**On the Pi:**

- [ ] Install Ollama:
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```
  **Why:** this downloads Ollama for 64-bit ARM, creates an `ollama` system user, and installs it as a
  systemd service, so it already starts at every boot. A warning that **no NVIDIA/AMD GPU** was found
  is expected: we run on the CPU.
- [ ] Check it's running:
  ```bash
  systemctl status ollama --no-pager
  ```
  Look for `active (running)`.
- [ ] Download the main model and a faster backup (~2.0 GB + ~1.3 GB):
  ```bash
  ollama pull llama3.2:3b
  ollama pull llama3.2:1b
  ```
  **Why Llama 3.2 (by Meta):** its license allows commercial use, so the honest answer to "could a company deploy this?" is yes.
  It follows instructions well, and it officially supports German and Hindi as well as English.
  **Why two:** 3B is smarter; 1B is roughly two to three times faster. You'll pick one after measuring.
  This is the **last time the Pi needs internet.**
- [ ] Optional backup: `ollama pull qwen2.5:3b` (~1.9 GB). Qwen is often stronger at pulling exact codes and numbers out of
  documents, but its 3B version is licensed for research only. Handy if Llama gets fields wrong in Phase 5.
- [ ] Your first conversation with on-device AI:
  ```bash
  ollama run llama3.2:3b --verbose
  ```
  Ask something, for example: *What does the Incoterm FCA mean, in two sentences?* Then type `/bye` to exit.
  **Look at `eval rate`** in the stats it prints: that's the model's writing speed in tokens per second. Note it for your pitch
  ("6 tokens per second on a €100 board, about 10 watts").
- [ ] Do the same for `llama3.2:1b` and note its eval rate too.
- [ ] Optional: in a second SSH window, run `htop` (exit with `q`) while the model answers.
      You'll see all 4 cores at 100%: that's the "datacenter" working.

---

## Phase 5: Deploy and test the app (2:00–2:45)

The app is already written and tested (see [What's in this folder](#whats-in-this-folder)). It uses only
Python's standard library, so **there is nothing to `pip install`** (Raspberry Pi OS already ships Python 3).

**On the Mac:**

- [ ] Copy the project to the Pi. Type `scp -r ` (with a trailing space), **drag the `zerocloud` folder from Finder into
      the Terminal window**, then type ` zero@zerocloud.local:~/` and press Enter. The line looks like:
  ```bash
  scp -r /path/to/zerocloud zero@zerocloud.local:~/
  ```
  **Why drag and drop:** it types the folder's exact path for you, spaces included.
  Re-run the same command whenever you change a file on the Mac.

**On the Pi:**

- [ ] Start the app by hand the first time, so you can see its log:
  ```bash
  cd ~/zerocloud
  python3 app.py
  ```
  Wait for `model llama3.2:3b loaded and warm` (10–40 s: the model is being read from the SD card into RAM).
  **Why by hand first:** any error shows directly on screen. In Phase 6 it moves into the background.
  **Already did Phase 6?** Then the app runs as a service, and `python3 app.py` fails with "Address already in use".
  Restart the service instead to load new files: `sudo systemctl restart zerocloud`.

**On the Mac:**

- [ ] Open **<http://zerocloud.local:8080>** in your browser.
  - Top right should say the device is a Raspberry Pi 5 and `llama3.2:3b in RAM` (or `1b`).
  - The network pill will be amber ("Internet connected"), because you're still on the setup Wi-Fi. That's expected for now.
- [ ] On the **① Gate** tab, run the three sample emails with **Run step 0**. Expected results:

  | Sample (all fictional) | Rules | Local AI | Decision |
  |---|---|---|---|
  | **1. Clean booking** | nothing found, ~1 ms | not needed | 🟢 CLEAR → Sent to ELM ✓ |
  | **2. Sensitive booking** | controlled HS codes, confidential terms, spec sheet | not needed | 🔴 HOLD → Kept on this box |
  | **3. Unclear booking** | only unclear words | reads it on the box | 🔴 HOLD: drone parts with possible military use |

  Each decision shows a **Logged ✓** line with time, who decided and the case number. Below it, the local AI then
  fills in **Booking details** as structured JSON (shipper, consignee, route, mode, Incoterm, ready date, goods with HS codes):
  "→ Handed to ELM as structured data" for the clean booking, "→ Stays on this box" for held ones.
  An HS code the AI didn't copy from the email is flagged "not found in the email".
- [ ] On **② Compliance review**, open the held unclear booking (sample 3):
  - Under **Ask Forto's knowledge base**, click **What should I ask the customer?** The local AI answers from the
    knowledge-base documents on the Pi and lists the sections it used.
  - Click **Reject** with the reason box empty: it refuses ("Enter a rejection reason"). Type a reason, e.g.
    "End-use statement missing for the drone components", and click **Reject** again.
  - **Case history** at the bottom shows every step: screened, booking extracted, knowledge base asked, rejected,
    with your name and reason.
- [ ] Open the held sensitive booking (sample 2) and click **Approve → send to ELM**. It turns green: "Approved → Sent to ELM ✓".
- [ ] **Check the AI speed** on sample 3: the "Local AI ✓ … s" label after the decision. The model writes just four short
      fields, so expect roughly **10–20 s** with `llama3.2:3b` and **5–10 s** with `llama3.2:1b`:
  - **Under ~20 s:** keep `llama3.2:3b`. It judges unclear emails better than the 1B model.
  - **Over ~20 s:** switch the service to the faster model: `./setup/install-service.sh llama3.2:1b`, then test again.
  - **Much slower than that?** Check the Pi isn't overheating: `vcgencmd get_throttled` should show `0x0`. A Pi that's
    slowing itself down because the fan isn't working can take several times longer.
- [ ] Before rehearsing, click **Reset demo** at the bottom of the page. It clears the cases; the audit log file on the Pi
      keeps a record (view it with `cat ~/zerocloud/data/audit.jsonl` if anyone asks).
- [ ] Stop the app with `Ctrl+C`.

---

## Phase 6: Go off-grid (2:45–3:30)

### 6a. Start automatically at boot

**On the Pi:**

```bash
cd ~/zerocloud
./setup/install-service.sh llama3.2:3b      # or llama3.2:1b if you picked that
```

**Why:** this registers the app as a systemd service (like Ollama already is), so it starts on every boot and restarts if it crashes.
Check it: refresh <http://zerocloud.local:8080> on the Mac. Live logs: `journalctl -u zerocloud -f` (exit with `Ctrl+C`).

### 6b. Make the Pi its own Wi-Fi network

**On the Pi:**

- [ ] First, note the name of your current Wi-Fi connection (you'll need it to go back online):
  ```bash
  nmcli connection show
  ```
- [ ] Create the hotspot (pick a password of at least 8 characters, e.g. `freight2026`):
  ```bash
  ./setup/hotspot.sh freight2026
  ```
  **Why:** NetworkManager (`nmcli`) turns the Pi's Wi-Fi chip into an access point called **ZeroCloud**. The Pi takes address `10.42.0.1`
  and hands out addresses to anyone who joins. There's no uplink, so nothing on this network can reach the internet.
  It's created *without* autostart on purpose: if anything goes wrong, unplugging and replugging the power brings the Pi back on your normal Wi-Fi.
- [ ] Turn it on:
  ```bash
  sudo nmcli connection up ZeroCloud
  ```
  **Your SSH session will freeze.** That's expected, because the Pi just left the network you were connected through. Close that Terminal window.

**On the Mac:**

- [ ] Click the Wi-Fi icon in the menu bar → join **ZeroCloud** with your password. macOS will say "No Internet Connection". That's the point.
- [ ] Open **<http://10.42.0.1:8080>**. The network pill should now be **green: "Air-gapped: no internet"**.
- [ ] Run a sample to confirm it works fully offline.
- [ ] SSH back in over the hotspot: `ssh zero@10.42.0.1`, and prove it from the Pi's side:
  ```bash
  ping -c 2 1.1.1.1
  ```
  You should get a failure ("Network is unreachable" or 100% packet loss).
- [ ] It works, so make the hotspot start at every boot:
  ```bash
  sudo nmcli connection modify ZeroCloud connection.autoconnect yes connection.autoconnect-priority 100
  ```

### 6c. The cold-boot test (the most important test of the day)

- [ ] On the Pi: `sudo reboot`. Or better, pull the power cable and plug it back in, exactly as on stage.
- [ ] Wait ~90 seconds. On the Mac, join **ZeroCloud** again and open <http://10.42.0.1:8080>.
- [ ] Wait until the model pill shows **in RAM**, then run the messy booking email.
- [ ] If this works, your demo works. Bookmark `http://10.42.0.1:8080` in the browser.

**To go back online later** (for example to change something that needs internet), SSH in via `ssh zero@10.42.0.1`, then:
```bash
sudo nmcli connection modify ZeroCloud connection.autoconnect no
sudo nmcli connection down ZeroCloud
```
The Pi rejoins your normal Wi-Fi within ~20 seconds. Move the Mac back to that Wi-Fi and use `ssh zero@zerocloud.local`.
To copy changed files while the Pi is a hotspot, use `zero@10.42.0.1` instead of `zero@zerocloud.local` in the `scp` command.

---

## Phase 7: Pitch, rehearse, record a backup (3:30–4:30)

### Three slides are enough

1. **The problem.** "Some booking emails must not reach cloud AI, and you only know which ones after reading them."
   Examples: controlled goods, technical specs, confidential contracts.
2. **Step 0 before ELM.** The architecture diagram from the top of this guide, plus the box:
   *€100-class prototype · ~10 W · no AI provider.*
3. **The ask.** "Test it with Compliance on 200 past emails." Three numbers to measure: share cleared automatically,
   sensitive emails missed (target: zero), review time per held case.

### Say it precisely

These are the fixes from the design critique. They keep the claims accurate if a judge pushes back:

| Instead of… | Say… | Why |
|---|---|---|
| "Compliance confirmed these rules" | "**Proposed** rules, to be confirmed by Compliance" | The two routing rules are your working assumptions |
| "It keeps data off the cloud" | "It keeps sensitive cases **away from cloud AI** until a compliance manager approves them" | Emails already live in our cloud mailbox; the policy is about AI processing |
| "It runs offline in production" | "On our own server, **no external AI provider**" | A production gateway must be on the network to receive emails and pass them to ELM. The Pi demo runs air-gapped to make the point visible. |
| "The AI never makes mistakes" | "**When in doubt, it holds.** The AI only reads; lists and a human decide." | No model can promise zero hallucination; the design makes errors harmless |
| "A Pi screens all our email" | "Rules screen every email in milliseconds; the model only reads the unclear ones. **The Pi is the prototype**; the architecture scales on our own hardware." | A Pi handles roughly 60–100 model reads an hour |
| "It screens everything" | "Sanctions screening **stays in TMS**. The gateway adds what TMS doesn't check before AI automation: dual-use goods, technical data and confidential material." | Avoids duplicating an existing control |
| (nothing) | "Every hold, approval and rejection is **logged**, with the control-list version" | Compliance will ask who approved what, when, and against which list |
| "Why not cloud AI under a data agreement?" | "For generic emails, fine: those go to ELM. The point is the ones you can't know are sensitive until they've been read." | The strongest answer to the most likely objection |

### The 2:55 script

| Time | Beat | You say / do |
|---|---|---|
| 0:00–0:25 | **Problem** (slide 1) | "ELM will let AI handle our bookings. But some emails carry controlled goods, technical specs or confidential contracts, which our GenAI rules keep out of cloud AI. And you only know after you've read them." |
| 0:25–0:40 | **Solution** | Hold up the Pi. "Step zero before ELM, on our own hardware. No AI provider, not even internet: see the green 'air-gapped' light." |
| 0:40–1:00 | **Clean booking** | *Run step 0*. "A normal booking: cleared in a millisecond, sent to ELM." Point at the JSON filling in: "And the local AI turns the email into structured booking data for ELM." |
| 1:00–1:15 | **Sensitive booking** | *Run step 0*. "Controlled electronics and a confidential spec sheet: held on the box." (Don't wait for its JSON.) |
| 1:15–1:45 | **Unclear booking** | *Run step 0*. "No rule fires. The local AI reads it, recognises drone parts, and holds it. Reading it *here* is the whole point." (Say this while the AI reads, ~10–20 s.) |
| 1:45–2:25 | **Human decides, with AI help** | *Review case →* click **What should I ask the customer?** "The compliance manager asks our own procedures, on this box." (~10–20 s.) Then type the reason "End-use statement missing" and **Reject**: "Decision, reason and name: in the case history." |
| 2:25–2:55 | **Why it matters + ask** (slides 2–3) | "Most bookings flow untouched; the sensitive few never reach cloud AI unreviewed. Next: test it with Compliance on 200 past emails. ZeroCloud: step zero before ELM." |

**Timing tip:** the two AI waits (unclear booking and the knowledge-base answer) are the slowest parts. Rehearse with a
timer on the Pi itself. If you're over 3:00, skip the sensitive booking: the unclear one makes the stronger point.

### Rehearse and protect yourself

- [ ] Rehearse the full script **3 times with a timer**. Don't cut the live run: it's your strongest moment.
- [ ] **Record a backup video:** on the Mac press **⌘+Shift+5** → *Record Entire Screen*, run the script's demo steps (samples 1, 2 and 3, the knowledge-base question, then reject with a reason), then stop from the menu bar.
      **Why:** if the projector, venue Wi-Fi interference or anything else fails, you play the video and keep going. Judges respect a calm fallback.
- [ ] In the browser, zoom to ~125% (⌘ +) so the back row can read it.

### Pre-stage checklist (10 minutes before you present)

- [ ] Pi powered on for at least 5 minutes (model loaded, warm)
- [ ] Mac joined to **ZeroCloud** Wi-Fi, browser open at `http://10.42.0.1:8080`, pills green
- [ ] One warm-up run of sample 3 done (the first model read after boot is the slowest)
- [ ] **Reset demo** clicked (bottom of the page), so the review list starts empty
- [ ] Mac notifications off (Control Centre → Focus → Do Not Disturb), other tabs closed
- [ ] Backup video on the desktop
- [ ] The Pi is physically visible to the audience. The point is that the AI is in that little box.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ssh: Could not resolve hostname zerocloud.local` | Wait 2 more minutes (first boot is slow). Make sure the Mac is on the **same** Wi-Fi you entered in Imager. Check the SSID and password spelling, then re-flash. Venue networks sometimes block devices from seeing each other: use your phone's hotspot instead. |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED` (after re-flashing) | On the Mac: `ssh-keygen -R zerocloud.local`, then connect again. |
| `ollama: command not found` | The install didn't finish. Check internet on the Pi: `ping -c 2 ollama.com`, then re-run the install command. |
| App says "Can't reach Ollama" | `sudo systemctl restart ollama`, wait 10 s, retry. Check `systemctl status ollama --no-pager`. |
| App says "model … not found" | `ollama pull llama3.2:3b` (needs internet: go back online first, see Phase 6). |
| `OSError: [Errno 98] Address already in use` | The app already runs as a service (Phase 6). Don't start it by hand; use `sudo systemctl restart zerocloud`, and `journalctl -u zerocloud -f` for the live log. |
| Every unclear email is held, even harmless ones | Fail-closed working as designed: the small model wasn't confident. Try `llama3.2:3b` instead of `1b` (`./setup/install-service.sh llama3.2:3b`). |
| Model pill stuck on "loading…" | Normal for up to ~60 s after boot. If longer: `journalctl -u zerocloud -n 30 --no-pager`. |
| Answers are very slow / temperature over 80 °C | Check `vcgencmd get_throttled`. Anything other than `0x0` means a power or cooling problem. Use the kit's power supply and fan. Or switch to the 1B model: `./setup/install-service.sh llama3.2:1b`. |
| "ZeroCloud" Wi-Fi doesn't appear on the Mac | SSH in via the normal Wi-Fi (pull the power to reboot, since the hotspot isn't on autostart until 6b's last step), then run `sudo nmcli connection up ZeroCloud` and read the error. Check the Wi-Fi country was set in Imager. |
| Mac keeps jumping back to the venue Wi-Fi | System Settings → Wi-Fi → venue network → ⓘ → turn off **Auto-Join** during the demo. |
| Locked out completely | Pull the power and plug it back in. If the hotspot is on autostart, join **ZeroCloud** and `ssh zero@10.42.0.1`. If all else fails, re-flash the card: Phases 2–6 take about an hour with this guide. |

---

## What's in this folder

| File | What it does |
|---|---|
| `app.py` | The whole gateway: stage-1 rules, the local-AI prompt and answer form, the fail-closed decision, approval, the audit log and the simulated ELM hand-off. |
| `rules/control_list.json` | The **demo** control list: controlled HS codes and keywords for dual-use goods, technical data and confidential terms. Illustrative, not legal advice. Edit it and restart the app to change what's screened. |
| `static/index.html` | The web page you demo, with two tabs: Gate and Compliance review. Loads no external fonts or scripts, so it works with zero internet. |
| `samples/*.txt` | The three fictional demo emails: clean, sensitive, unclear. Add your own `.txt` files here and they appear as buttons automatically. |
| `knowledge/*.md` | The **demo** knowledge base the reviewer can ask: dual-use procedure, GenAI data rules (from Compliance's input), questions for customers, technical data, decisions. Clearly labelled demo content, not official Forto policy. Replace with real procedures and restart the app; each `##` section becomes a searchable piece. |
| `data/` (created on first use) | Cases, the append-only audit log (`audit.jsonl`) and the simulated ELM hand-off, stored on the Pi only. |
| `setup/install-service.sh` | Makes the app start at every boot (systemd). |
| `setup/hotspot.sh` | Creates the "ZeroCloud" Wi-Fi network broadcast by the Pi. |

Settings you can change through environment variables: `ZC_MODEL` (default `llama3.2:3b`), `ZC_PORT` (default `8080`), `OLLAMA_URL`.

## If you finish early (stretch ideas)

- Ask Compliance for the HS codes they actually use for dual-use screening, and put them into `rules/control_list.json`.
- Add an anonymised real case as a fifth sample (no customer names). It becomes a button automatically.
- Run the three samples with `ZC_MODEL=qwen2.5:3b python3 app.py` and compare decisions and speed with Llama: a nice "we benchmarked" slide.
