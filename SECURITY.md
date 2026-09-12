# Security policy and privacy model

## Intended deployment

This is a local, single-owner rehearsal tool, not a public multi-tenant service.
The API binds to loopback by default and requires a per-installation bearer
token for all rehearsal data and control routes. Only the generic controller
shell and minimal health endpoint are public.

Authentication does not encrypt HTTP. Prefer loopback plus SSH tunneling for
remote PC control. A network/browser deployment needs HTTPS, exact allowed
origins, restricted access and a production server configuration. Never expose
the development server through port forwarding or an anonymous tunnel.

The browser does not store the token in cookies, local/session storage or URLs.
It sends an explicit bearer header. Foreign browser origins are rejected;
JSON endpoints do not accept form/text bodies as JSON. Keep the token private
and revoke it if compromised.

## Local trust boundary

Anyone with access to your OS account, token file or runtime directories may be
able to control the app or read saved information. Authentication is not
multi-user authorization. Use disk encryption, a locked OS account, restrictive
filesystem permissions, regular updates and trusted extensions/software.

The app does not promise resistance to a compromised host, hostile USB device,
malicious browser extension, physical access or an authorized destructive
controller. An authorized controller can replace uploaded slides, alter
coaching, start sessions and thereby activate optional recognition.

## Data handled locally

- Uploaded slide images are saved on the host.
- Session title, cue text, timestamps and aggregate camera/speech measurements
  are persisted in local state.
- Raw camera frames, raw audio and recognized transcripts are not intentionally
  recorded by the application. Inference processes handle them in memory.
- Facial-expression outputs are uncertain estimates, not feelings or sensitive
  personal attributes, and are not used for delivery coaching.
- Initial dependency/model downloads contact their public package/upstream
  servers. Installed inference does not require a cloud analysis service.

Runtime data is not automatically encrypted or expired. Protect it, establish
your own retention policy and remove it when no longer needed. Logs and crash
diagnostics may include local paths or error context; redact before sharing.

## Publishing changes

Never add real credentials, tokens, private URLs/IPs, camera serials, personal
paths, accounts, presentations, recordings, logs or state files to Git. Do not
import old repositories or backups. `.gitignore` alone cannot prevent a forced
add or scrub previous commits.

Use an explicit publication allowlist, review every staged path and diff,
inspect image contents/metadata, scan for secrets and audit dependencies before
publishing. Generated UI demo images must be synthetic and reproducible.
Identifiable hardware photographs require explicit publication approval;
remove metadata and redact browser details before committing public copies.
Dependencies and upstream model licenses must remain respected.

Security scans cover known patterns and advisories, not all possible
vulnerabilities. The application and its dependencies are provided without
warranty; keep reviewing and updating them.

## Reporting a vulnerability

Use the repository's **Security > Report a vulnerability** private reporting
option when available. Do not put tokens, private user content or a working
exploit against a real installation in a public issue.

If private reporting is unavailable, open a minimal issue requesting a private
contact channel **without disclosing the vulnerability or sensitive material**.
Rotate any exposed credentials immediately and remove network exposure while
investigating.
