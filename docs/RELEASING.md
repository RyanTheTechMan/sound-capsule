# Releasing Sound Capsule

The manual **Build draft release** workflow builds both platforms and creates a
GitHub draft. Its macOS job requires Developer ID signing and Apple
notarization; it will not package an unsigned macOS release.

## Apple credentials

Sound Capsule needs these seven encrypted GitHub Actions secrets:

| Secret | Value |
| --- | --- |
| `MACOS_CERTIFICATE_BASE64` | A Base64-encoded `.p12` containing a **Developer ID Application** certificate and its private key |
| `MACOS_CERTIFICATE_PASSWORD` | The password chosen when exporting that `.p12` |
| `MACOS_INSTALLER_CERTIFICATE_BASE64` | A Base64-encoded `.p12` containing a **Developer ID Installer** certificate and its private key |
| `MACOS_INSTALLER_CERTIFICATE_PASSWORD` | The password chosen when exporting the installer `.p12` |
| `APPLE_ID` | The email address used for the Apple developer account |
| `APPLE_APP_SPECIFIC_PASSWORD` | An app-specific password created for notarization, not the normal Apple account password |
| `APPLE_TEAM_ID` | The 10-character Apple Developer Team ID that issued the certificate |

No provisioning profile is required for Sound Capsule's current capabilities.
The workflow creates its temporary keychain password itself.

If another repository already uses the same active Developer ID identities and
notarization account, the same seven values can be added to this repository.
Repository secrets are not shared automatically unless they were configured as
organization secrets with access granted to both repositories.

## Export the certificate

1. In Xcode or Certificates, Identifiers & Profiles, create or confirm both a
   **Developer ID Application** certificate and a **Developer ID Installer**
   certificate. They are separate identities.
2. In Keychain Access, find that certificate under **My Certificates**. Confirm
   that it expands to show its private key.
3. Export each certificate with its private key as a separate `.p12` and protect
   each export with a strong password. The passwords become the corresponding
   application and installer certificate password secrets.
4. Encode each file and copy it to the clipboard:

   ```sh
   base64 -i DeveloperIDApplication.p12 | pbcopy
   ```

   Paste the application result into `MACOS_CERTIFICATE_BASE64`, then repeat for
   the installer P12 and `MACOS_INSTALLER_CERTIFICATE_BASE64`.

Never commit the `.p12`, its Base64 value, or either password.

## Create the notarization password

Create an app-specific password from the Apple account's **Sign-In and
Security** settings. Use a recognizable label such as `Sound Capsule GitHub`.
Store the generated value as `APPLE_APP_SPECIFIC_PASSWORD`.

The Team ID is available in the Apple Developer membership details. The Apple
ID must have access to that team.

## Add the repository secrets

On GitHub, open **Settings → Secrets and variables → Actions**, choose **New
repository secret**, and add all seven names exactly as shown above.

The workflow imports both P12 files into an ephemeral keychain, verifies their
identity types and Team ID, and signs the app, VST3, and PKG. It notarizes and
staples the app/VST3 and the final installer independently, validates Gatekeeper,
then produces the downloadable ZIP and PKG. The Windows MSI remains unsigned and
will produce an unknown-publisher warning until Authenticode signing is added.

## Prepare and publish a release

1. Update `helper/soundcapsule/__init__.py` and add the matching dated section
   to `CHANGELOG.md`.
2. Push the release commit.
3. Open **Actions → Build draft release → Run workflow**, select the desired
   branch or commit, and enter the version without a leading `v`.
4. Review the draft's notes, ZIP files, PKG, MSI, checksums, and workflow logs.
5. Test the PKG on macOS 13+ and the MSI on Windows 10/11 when possible, then
   publish the draft.

Native uninstall removes the application, packaged setup payload, shortcuts, and
selected VST3. Windows also removes the current user's generated helper environment
and FL bridge. Settings and capsule libraries are retained for reinstall, and uv is
never removed because it may be used by other projects.
