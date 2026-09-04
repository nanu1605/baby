# Code signing

Baby currently ships **unsigned**. This page says why, what that means for you,
and how a signed build gets made when the certificate exists.

## Why unsigned

Windows SmartScreen trusts a publisher based on reputation, and reputation is
attached to a code-signing certificate. Getting one costs money:

| Option | Cost | What it gets you |
|---|---|---|
| OV certificate | ~$200-400/year | Signed, but reputation still builds from zero |
| EV certificate | ~$300-600/year | Immediate SmartScreen reputation; needs a hardware token |
| Azure Trusted Signing | ~$10/month | Signed; requires a verified business identity |
| **SignPath Foundation** | **free** | Free signing for qualifying open-source projects |

The decision for v6 was to publish free and ship unsigned rather than delay the
release, with **SignPath Foundation** as the track to a genuinely trusted
signature at no cost.

## What unsigned means for you

- SmartScreen shows *"Windows protected your PC"*, and the Run button is behind
  **More info**. See [INSTALL.md](INSTALL.md#2-windows-will-warn-you-here-is-why-and-what-to-do).
- Antivirus products flag unsigned installers more readily.
- **Verify the SHA-256 checksum** published with each release before running it.
  That is what actually tells you the file is the one that was published; a
  signature would tell you who published it.

## SignPath Foundation

SignPath's free tier requires the project to be open source under an
**OSI-approved license**.

That prerequisite is now met: the repository is **MIT licensed** (see
[`LICENSE`](../LICENSE)), which is OSI-approved. A SignPath Foundation
application can be made.

What the application needs from you:

- The public repository URL and its `LICENSE`.
- A maintainer identity SignPath can verify.
- A build that produces the artifact to be signed — the NSIS installer from
  `npm --prefix ui/shell run build`.

Approval is a manual review on their side, so it takes time. Until it lands,
releases ship unsigned with checksums, as described above.

## The hook is already wired

Signing does not need a code change when the certificate arrives. Tauri signs
the installer through `bundle.windows.signCommand`, so it is a config addition:

```jsonc
// ui/shell/src-tauri/tauri.conf.json
"bundle": {
  "windows": {
    "signCommand": "<your signing tool> %1"
  }
}
```

Tauri substitutes `%1` with the path of each artifact to sign. Keep the actual
credential out of the repository — pass it through an environment variable or a
CI secret. Nothing else in the build changes.

## Release checklist

For each release, publish alongside the `.exe`:

1. `SHA256SUMS.txt` — generated with:

```powershell
Get-FileHash .\Baby_6.0.1_x64-setup.exe -Algorithm SHA256 | Format-List
```

2. A note in the release body pointing at the SmartScreen walkthrough, so a
   first-time user is not left guessing at a scary blue box.
