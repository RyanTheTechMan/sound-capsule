# Releasing Sound Capsule

The manual **Build draft release** workflow builds both platforms and creates a
GitHub draft. Its macOS job requires Developer ID signing and Apple
notarization; it will not package an unsigned macOS release.

## Apple credentials

Sound Capsule needs these five encrypted GitHub Actions secrets:

| Secret | Value |
| --- | --- |
| `MACOS_CERTIFICATE_BASE64` | A Base64-encoded `.p12` containing a **Developer ID Application** certificate and its private key |
| `MACOS_CERTIFICATE_PASSWORD` | The password chosen when exporting that `.p12` |
| `APPLE_ID` | The email address used for the Apple developer account |
| `APPLE_APP_SPECIFIC_PASSWORD` | An app-specific password created for notarization, not the normal Apple account password |
| `APPLE_TEAM_ID` | The 10-character Apple Developer Team ID that issued the certificate |

No provisioning profile is required for Sound Capsule's current capabilities.
The workflow creates its temporary keychain password itself.

If another repository already uses the same active Developer ID identity, the
same five values can be added to this repository. Repository secrets are not
shared automatically unless they were configured as organization secrets with
access granted to both repositories.

## Export the certificate

1. In Xcode, open **Settings → Accounts**, select the Apple developer account,
   and use **Manage Certificates** to create or confirm a **Developer ID
   Application** certificate.
2. In Keychain Access, find that certificate under **My Certificates**. Confirm
   that it expands to show its private key.
3. Select the certificate and private key together, export them as a `.p12`,
   and protect the export with a strong password. That password becomes
   `MACOS_CERTIFICATE_PASSWORD`.
4. Encode the file and copy it to the clipboard:

   ```sh
   base64 -i DeveloperIDApplication.p12 | pbcopy
   ```

   Paste the result into `MACOS_CERTIFICATE_BASE64`.

Never commit the `.p12`, its Base64 value, or either password.

## Create the notarization password

Create an app-specific password from the Apple account's **Sign-In and
Security** settings. Use a recognizable label such as `Sound Capsule GitHub`.
Store the generated value as `APPLE_APP_SPECIFIC_PASSWORD`.

The Team ID is available in the Apple Developer membership details. The Apple
ID must have access to that team.

## Add the repository secrets

On GitHub, open **Settings → Secrets and variables → Actions**, choose **New
repository secret**, and add all five names exactly as shown above.

The workflow imports the P12 into an ephemeral keychain, checks that it is a
Developer ID Application identity from `APPLE_TEAM_ID`, and signs both bundles
with hardened runtime and a secure timestamp. It then submits an archive
containing both the standalone app and VST3 to Apple's notary service, prints
the notary log, staples and validates both tickets, verifies Gatekeeper
assessment for the app, and only then creates the downloadable ZIP.

## Prepare and publish a release

1. Update `helper/soundcapsule/__init__.py` and add the matching dated section
   to `CHANGELOG.md`.
2. Push the release commit.
3. Open **Actions → Build draft release → Run workflow**, select the desired
   branch or commit, and enter the version without a leading `v`.
4. Review the draft's notes, ZIP files, checksums, and workflow logs.
5. Test the macOS ZIP on a clean Mac when possible, then publish the draft.
