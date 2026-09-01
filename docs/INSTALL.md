# Installing Baby on Windows

Baby ships as a single Windows installer. You do not need Python, Git, or a
developer setup — the installer brings everything it needs.

- **Windows 11**, 64-bit
- **~8 GB free disk** for a Full install (~3 GB for cloud-only)
- **An internet connection for the first run** (the models are downloaded then,
  not bundled — a ~1.6 GB installer would otherwise be a ~9 GB one)
- **An NVIDIA GPU with 8 GB VRAM** if you want the local brain. Without one,
  pick cloud-only; Baby will tell you which fits your machine.

---

## 1. Download

Get `Baby_<version>_x64-setup.exe` from the
[Releases page](https://github.com/nanu1605/baby/releases).

Each release also publishes a `SHA256SUMS.txt`. To check your download matches:

```powershell
Get-FileHash .\Baby_6.0.0_x64-setup.exe -Algorithm SHA256
```

Compare the result with the line for that file in `SHA256SUMS.txt`. If they
differ, delete the file and download it again — do not run it.

## 2. Windows will warn you. Here is why, and what to do

Baby is **not code-signed yet**, so Windows SmartScreen shows a blue box saying
*"Windows protected your PC"* and hides the Run button behind **More info**.

That warning does not mean the file is malicious. It means nobody has paid for a
code-signing certificate for it — SmartScreen's reputation is bought, and an
unsigned build from a small project starts with none. See
[SIGNING.md](SIGNING.md) for what is being done about it.

To install anyway, after verifying the checksum above:

1. Click **More info**.
2. Click **Run anyway**.

If you would rather not run unsigned software, that is a completely reasonable
call. You can [run from source](../README.md#setup) instead, or wait for a
signed release.

## 3. Install

The installer is **per-user** and needs no administrator rights. It installs to
your own profile and adds a Start Menu entry.

One step may ask for permission: if your PC does not already have the
**Microsoft Visual C++ 2015-2022 runtime**, Baby installs it on first launch and
Windows will show a UAC prompt. That runtime is a Microsoft component several of
Baby's audio and AI libraries need in order to load at all. If you decline, Baby
will tell you what is missing and how to install it yourself.

## 4. First run

The first launch does the heavy lifting, with a progress screen:

1. **Builds its Python environment** (~1.5 GB). Resumable — if your connection
   drops, reopen Baby and it continues from where it stopped.
2. **Asks how you want to run it.** Baby checks your GPU and recommends one:
   - **Full** — a local model on your GPU plus cloud models. Works offline, and
     private conversations never leave the PC. Downloads several GB more.
   - **Cloud only** — no local model. Fastest to set up, but it needs an
     internet connection and an API key, and your messages go to the provider.
3. **Downloads what it needs**, with per-item progress. Also resumable.
4. **Asks for an API key.** Baby tests the key against the provider before
   accepting it, so a mistyped key is caught immediately rather than at the
   first message. A Full install can skip this and run entirely on your GPU; a
   cloud-only install needs at least the main key.
5. **Shows what Baby can do on your PC**, and asks you to acknowledge it.

Then it is ready.

### Where your data lives

Everything is under `%LOCALAPPDATA%\baby`:

| What | Where |
|---|---|
| Settings | `config.yaml` |
| API keys | `.env` (restricted so only your account can read it) |
| Conversations and memory | `baby.db` |
| Downloaded models | `models\` |
| Logs | `logs\baby.log` |

None of it is uploaded anywhere. There is no account and no telemetry.

---

## If something goes wrong

**Baby says setup failed.** Reopen it. Every step of first-run setup resumes
rather than restarting, so nothing already downloaded is lost. The message tells
you which of the usual causes it was — no internet, a corporate proxy, or not
enough disk space.

**Behind a corporate proxy.** Set `HTTPS_PROXY` and `HTTP_PROXY` in your
environment variables, then reopen Baby.

**A component installed but will not load.** That is almost always the missing
Visual C++ runtime. Install
[vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe) and reopen.

**Antivirus quarantined something.** Unsigned installers get flagged more often.
Verify the checksum, then allow the file if you are satisfied it is the one
published on the Releases page.

**Reporting a bug.** Baby can generate a diagnostics report that is safe to post
publicly — your API keys, your Windows username, and your name are removed
before you see it. It includes versions, what installed, what is failing, and
the tail of the log.

## Uninstalling

Uninstall from **Settings → Apps → Installed apps**, or the Start Menu entry.

The uninstaller offers a **"Delete application data"** checkbox:

- **Ticked** — `%LOCALAPPDATA%\baby` is removed: conversations, memory, models,
  and your API keys.
- **Unticked** — your data stays, so reinstalling picks up where you left off.
