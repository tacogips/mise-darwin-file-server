# mise-darwin

This repository rebuilds an Apple Silicon macOS development desktop formerly
managed in `tacogips/nix`, with mise as the entry point. It is intentionally
desktop-only: it is not a home-server or general-purpose host configuration.
It does not depend on the Nix Store or nix-darwin generations.

## New machine setup

On a new Apple Silicon Mac, install Homebrew if it is not already available,
then install mise, check out this repository,
and run the desktop bootstrap:

```sh
brew install mise
mkdir -p ~/gits/tacogips
git clone https://github.com/tacogips/mise-darwin-file-server.git ~/gits/tacogips/mise-darwin-file-server
cd ~/gits/tacogips/mise-darwin-file-server
mise trust
./bootstrap
```

The bootstrap configures this Mac as the development desktop. Sign in to the
Mac App Store first if App Store applications should be installed.
Dotfile sources resolve from the checkout, so the repository can live at another path.
Bootstrap reloads a running Ghostty after applying its managed config. If
Ghostty was already open when the dotfile link changed, run
`mise run ghostty:reload` to refresh new terminal windows without closing the app.

## Repository layout

```text
.miserc.toml                 Default platform and desktop environments
mise.toml                    Shared tools, environment variables, and tasks
mise.macos-arm64.toml        Apple Silicon packages, defaults, and dotfiles
mise.desktop.toml            Desktop GUI and Mac App Store applications
dotfiles/.config/nvim/       Lua and lazy.nvim configuration
assets/wallpapers/           Git-managed desktop wallpaper
dotfiles/.agents/skills/     Apple Gateway user skills
agent-user-scope/            Claude, Codex, Cursor, and Riela user assets
scripts/mise_darwin/         Standard-library Python provisioning commands
tests/                       Python provisioning unit tests
Brewfile.*                   Casks and third-party tap packages
```

`.miserc.toml` enables mise's platform environment detection and selects the
`desktop` environment by default. This repository targets development desktop
Macs only.

## mise environments (`-E`)

`-E NAME` tells mise to additionally load `mise.NAME.toml`. This repository
defines the following environments:

| `-E` value | Configuration file | Purpose |
| --- | --- | --- |
| `macos-arm64` | `mise.macos-arm64.toml` | Shared Apple Silicon packages, dotfiles, macOS defaults, and login shell |
| `desktop` | `mise.desktop.toml` | Development desktop packages, GUI applications, and Mac App Store apps |
Desktop commands use the defaults directly:

```sh
mise bootstrap status --missing

```

The early `.miserc.toml` setting is required because platform and desktop
environments must be selected before `mise.toml` is discovered.

Prefer the wrapper for applying the development desktop:

```text
./bootstrap              -> mise -E macos-arm64 -E desktop bootstrap --yes
```

List profiles or display the mapping at any time:

```sh
./bootstrap --list-profiles
./bootstrap --help
```

From Fish, update everything managed for the Desktop profile with one
command: mise tools, Homebrew formulae and casks, Mac App Store applications,
and installed packages from `tacogips/tap`:

```sh
mupgrade-taco
```

The same non-interactive operation is available directly as a mise task.
`upgrade-all` (and the `mupgrade-all` alias) is kept as an alias of the same
task. The Fish shortcuts use the checkout path shown above; from another
checkout, run the mise task in that directory:

```sh
mise run upgrade-taco
mise run upgrade-all
```

Run an individual update group when needed:

```sh
mise run upgrade-tools
mise run upgrade-packages
mise run upgrade-brew-common
mise run upgrade-brew-desktop
mise run upgrade-tacogips
```

`upgrade-tools` skips mise's global tracked-config pruning so that source
templates containing unrendered tool-version variables cannot block upgrades.

`upgrade-tacogips` updates only installed formulae and casks from
`tacogips/tap` without a confirmation prompt.

## Temporary packages

Run the latest official qFlipper release without copying it into
`/Applications`:

```sh
mise run shell -- qflipper
```

The task first searches mise's registry. When the name is not present there, it
queries the Homebrew Cask API and verifies the Cask's published SHA-256. DMGs
are stored under mise's cache root and extracted with macOS system tool
`/usr/bin/hdiutil`; its presence and executable permission are checked before
use. Ordinary mise tools are installed with `mise install-into`.
The extracted application is reused from `/tmp/qflipper-<sha256-prefix>` while
that directory exists. The temporary HTTP download cache is pruned after 30
days without reuse. No Fish alias is installed for qFlipper.

Unprefixed names search mise first and Homebrew Cask second. Prefix a name to
select one resolver explicitly:

```sh
mise run shell -- brew-cask:qflipper
mise run shell -- mise:ripgrep
```

For a Cask app, `shell` launches the temporary `.app`. For a mise CLI tool it
starts `$SHELL` with the tool's executable directories prepended to `PATH`.
Pass a command after another `--` to run it directly instead:

```sh
mise run shell -- mise:ripgrep -- rg --version
```

The same temporary installer can run another checksum-pinned HTTPS artifact:

```sh
mise run temp-install -- \
  --name example \
  --version 1.2.3 \
  --url https://example.com/example-1.2.3.tar.gz \
  --sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --executable bin/example \
  --bin-path bin \
  --format auto \
  -- --help
```

The managed destination is `/tmp/<name>-<sha256-prefix>`. Existing paths are
reused only when their ownership marker and artifact identity (name, URL, and
SHA-256) match; an unrelated path is never overwritten. Add `--dry-run` before
the final `--` to inspect the destination and command without installing or
launching the tool. Use `--install-only` to install and print the executable
path without launching it.
Fish aliases for mise itself and the upgrade tasks are kept separately in
`dotfiles/.config/fish/conf.d/mise-aliases.fish`. mise's generated completions
are loaded lazily from `dotfiles/.config/fish/completions/mise.fish`, so
subcommands and configured tasks are suggested for commands such as
`mise run` without adding work to shell startup.

## Set up a clean Mac

This repository targets Apple Silicon Macs with Homebrew installed under
`/opt/homebrew`.

First install the Xcode Command Line Tools. Wait for the installation dialog to
finish before continuing.

```sh
xcode-select --install
```

Run the [official Homebrew installer](https://docs.brew.sh/Installation), then
make Homebrew available in the current zsh session and future login sessions.

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

Install Git and mise. Homebrew is the
[recommended mise installation method on macOS](https://mise.jdx.dev/installing-mise.html).

```sh
brew install git mise
git --version
mise --version
```

Clone this public repository and apply the desktop configuration. Bootstrap may ask
for the macOS password when changing system settings or the login shell. Sign in
to the App Store first if Mac App Store applications should be installed.

```sh
mkdir -p ~/gits/tacogips
git clone https://github.com/tacogips/mise-darwin-file-server.git ~/gits/tacogips/mise-darwin-file-server
cd ~/gits/tacogips/mise-darwin-file-server
mise trust
./bootstrap
```

When bootstrap finishes, open a new Terminal window so the Homebrew Fish login
shell is active, then verify the complete configuration.

```sh
cd ~/gits/tacogips/mise-darwin-file-server
mise -E macos-arm64 -E desktop run verify
```

## Bootstrap an existing Mac

If Homebrew and mise are already installed, run:

```sh
mkdir -p ~/gits/tacogips
git clone https://github.com/tacogips/mise-darwin-file-server.git ~/gits/tacogips/mise-darwin-file-server
cd ~/gits/tacogips/mise-darwin-file-server
mise trust
./bootstrap
```

Preview changes or inspect missing resources without applying them:

```sh
mise -E macos-arm64 -E desktop bootstrap --dry-run
mise -E macos-arm64 -E desktop bootstrap status --missing
```

Bootstrap stops instead of overwriting conflicting Home Manager links or local
files. Inspect and back up those paths before explicitly allowing replacement:

```sh
mise -E macos-arm64 -E desktop bootstrap --yes --force-dotfiles
```

## Neovim

The Neovim configuration translates the previous NVF setup into the layered
layout used by `dotfile_nvim`:

```text
dotfiles/.config/nvim/
├── init.lua
├── ftplugin/
└── lua/taco/
    ├── core/       Options, keymaps, and autocommands
    └── plugins/    Editor, UI, LSP, and completion plugins
```

The first Neovim launch installs `lazy.nvim`. mise tools and bootstrap Homebrew
packages provide language servers, formatters, and supporting CLI tools. Commit
`lazy-lock.json` after reviewing the generated plugin lock.

Update Neovim plugins and Tree-sitter parsers through the shared mise entry
point, then review and commit the resulting `lazy-lock.json` changes:

```sh
mise run nvim:update
```

Yazi Git plugins are pinned in `package.toml`, and their pinned contents are
checked into the managed dotfiles. Sora colors are shared by Neovim, Ghostty,
Herdr, LazyGit, Yazi, bat/Delta, fzf, and eza. The Karabiner config
includes the migrated ANSI/Kana symbol mappings. Desktop bootstrap applies the
Git-managed Sora sea image to every macOS desktop. Apple Gateway skills are
linked one skill directory at a time so unrelated user skills are preserved.

## AI agent user scope and Riela

`agent-user-scope/` contains the migrated Claude commands, shared Claude/Codex
skills, including the Wrike Gateway skill, Cursor CLI configuration, Peekaboo
MCP configuration, and Cursor skill.
Bootstrap synchronizes only known assets and does not remove skills managed by
Riela or other installers. The old `envrc-generate` skill is intentionally
excluded because this setup does not use direnv.
Managed files replace legacy symlinks atomically, without writing into their
former Nix Store targets.

Codex keeps only `user-skill-router` implicitly visible. Detailed user skills
remain explicitly invocable and are loaded lazily through the router, avoiding
the 2% skill-metadata context limit without removing functionality. Bootstrap
derives the shared, Codex, Claude Code, and Cursor roots from one home-directory
path model instead of maintaining repeated absolute paths.

On desktop hosts, bootstrap installs the Riela application and all user-scope
workflow and skill packages listed in `agent-user-scope/riela-packages.txt`. If
the public `tacogips/riela-packages` checkout is absent, the installer clones it
under the standard checkout root. Bootstrap synchronizes Riela's default package
registry before installing packages from their local source paths. The Fable-led
`fable-and-improve-codex` skill
is installed for Claude Code only. Codex uses
`codex-design-and-implement-review-loop`, with GPT-6 Astra handling design,
design review, implementation-plan creation, and implementation-plan review;
GPT-5.6 Terra handling implementation; and GPT-5.6 SOL handling test-integrity,
independent, and adversarial review. The compact 18-step graph integrates
author self-checks into design, planning, and implementation, and combines
implementation-plan completion verification with commit preparation while
retaining the independent review gates. The Fable-led Codex and Opus workflows
likewise author design and implementation plans in one Fable execution before
their independent implementation review and final goal review.
Bootstrap retires only the known files from the former Codex projection of the
Fable skill and preserves any unrelated files in that directory. Before invoking the
Riela CLI installed by the desktop cask, bootstrap removes its quarantine
attribute only when present; this prevents a signed CLI update from stalling in
the macOS loader.

GitHub HTTPS authentication uses the `GITHUB_TOKEN` credential helper. Fish
provides `gh-token-export`, `gh-token-save-shared`, `gh-token-refresh`,
`gh-token-reset`, and `gh-clone`. Bootstrap installs or updates Herdr's
built-in Claude and Codex hooks
under each agent's home configuration while preserving their other settings.

For tracked changes, the Fish prompt shows added line totals in green and
deleted line totals in red next to the branch.

## Provisioning implementation

mise owns declarative packages, tools, dotfiles, defaults, and desktop
composition. Custom convergence is implemented as a standard-library Python
package under `scripts/mise_darwin/`; it covers agent assets, Herdr and Riela
integration, Docker configuration, AeroSpace display-topology workspace
assignment, verification, and guarded Nix removal. On
the desktop profile, workspaces 1 and 2 follow the first two external displays
while workspace 9 follows the built-in display; a single external display owns
both workspaces 1 and 2. Run its unit tests with:

```sh
mise run test
```

Only the clean-machine `bootstrap` wrapper and the pre-package Homebrew conflict
hook remain as POSIX shell because both can run before mise installs Python.

## Verification and Nix removal

Verify the migrated setup from a new shell:

```sh
mise -E macos-arm64 -E desktop run verify
```

The verification task runs `mise doctor` with the Homebrew `mise` launcher
first on PATH. This matches the installed shim symlink targets even though
`mise run` otherwise places a second path to the same executable first.

Only remove Nix after verification succeeds. Preview the destructive operation
first:

```sh
mise run nix:uninstall -- --confirm --dry-run
```

Then run the uninstall task:

```sh
mise run nix:uninstall -- --confirm
```

The task first removes nix-darwin. It uses the Determinate uninstaller when
available and otherwise follows the
[official legacy macOS multi-user uninstall procedure](https://nix.dev/manual/nix/stable/installation/uninstall#macos).
The legacy path verifies the APFS `/nix` volume, login shell, and user-scope
links before making changes. Modified system files are backed up under
`/var/backups/mise-darwin-nix-uninstall-*`.

Nix removal is irreversible. Keep the former configuration repository only as
a migration reference until this repository passes verification.

## Home file server on the Mac mini

Load `mise.file-server.toml` in addition to the Apple Silicon and desktop
configuration on the Mac mini:

```sh
mise -E macos-arm64 -E desktop -E file-server config ls
```

This overlay declares Kopia and smartmontools, a daily 03:00 backup LaunchAgent,
a five-minute recovery check LaunchAgent, an AC-power sleep-prevention
LaunchAgent, and guarded setup/status tasks. The agents use the documented clone path
`~/gits/tacogips/mise-darwin-file-server`; clone there before applying it.
Tailscale remains the Mac App Store package in the desktop profile. Sign in to
Tailscale on the server and clients; do not expose SMB on the public Internet.
Run `mise -E macos-arm64 -E desktop -E file-server run file-server:power` to
keep the server awake on AC power, enable wake on network access, and restart
after a power outage. This task asks for `sudo` only if a setting has drifted.
The `caffeinate -is` agent also prevents idle sleep while the user session is
running, without keeping the display awake.

Connect both intended RAID disks and the separate backup disk. Before erasing
anything, list the current whole disks and their partitions:

```sh
mise -E macos-arm64 -E desktop -E file-server run file-server:disks
mise -E macos-arm64 -E desktop -E file-server run file-server:storage-plan -- \
  --dock1 diskN --dock2 diskM --backup diskP
```

Replace the placeholders only after mapping the physical bays to the live
identifiers. The plan verifies three distinct, writable, external whole disks,
shows their existing partitions, and prints the exact commands without running
them. Disk numbers can change after reconnection, so rerun the plan immediately
before any erase. Also inspect `diskutil info diskN` and any available enclosure
serial or bay labels. Confirm
that existing data is backed up. The two Dock disks become an [AppleRAID mirror](https://support.apple.com/guide/disk-utility/create-a-disk-set-dskua23150fd/mac)
named `FileServer`, formatted APFS; the third disk is a separate GUID/APFS volume
named `FileServerBackup`. Creating the mirror and formatting the backup disk
**erase their existing contents**. Use Disk Utility's RAID Assistant or the
corresponding `diskutil AppleRAID create mirror` and `diskutil eraseDisk`
commands only after confirming the exact live disk identifiers. Do not use
internal disks. Keep the backup disk in a separate enclosure if possible: a
third disk in the same multi-bay USB bridge still shares its power supply and
controller with the RAID members. Some
USB bridges do not expose per-drive SMART data to smartmontools; if so, use
mount, RAID, backup, and enclosure health checks rather than treating a missing
SMART result as proof that the HDD is healthy.

After the volumes appear at `/Volumes/FileServer` and
`/Volumes/FileServerBackup`, run:

```sh
mise -E macos-arm64 -E desktop -E file-server run file-server:enroll
mise -E macos-arm64 -E desktop -E file-server run file-server:share
mise -E macos-arm64 -E desktop -E file-server run file-server:enable-smb
mise -E macos-arm64 -E desktop -E file-server run file-server:init-repository
mise -E macos-arm64 -E desktop -E file-server run file-server:backup
mise -E macos-arm64 -E desktop -E file-server run file-server:status
mise -E macos-arm64 -E desktop -E file-server run file-server:reconcile
mise -E macos-arm64 -E desktop -E file-server bootstrap macos launchd-agents apply --yes
```

Enrollment records the exact two volume UUIDs in
`~/.config/mise-darwin/file-server.json` (a machine-local file, never commit it).
After the mirror and backup volume exist, record the physical roles separately:

```sh
mise -E macos-arm64 -E desktop -E file-server run file-server:record-disks -- \
  --dock1 diskN --dock2 diskM --backup diskP
```

This verifies that the two selected disks are the online AppleRAID members and
the third backs `FileServerBackup`, then writes an owner-only inventory at
`.mise/file-server-disks.json`. It records media labels, sizes, RAID member
UUIDs, and volume UUIDs, but not transient `diskN` numbers. The `.mise/`
directory is gitignored because these identifiers belong to this machine.
Repeating the task is safe when roles match; it refuses to overwrite a changed
layout. Review this inventory and the live `file-server:disks` output before
adding or replacing a disk. The recorded roles are not erase targets.

The backup job refuses to run if either disk is absent, the mount names are
wrong, or Kopia points at a different repository. The status task checks that
both AppleRAID members are online and requires an error-free snapshot of the
intended share from the last 36 hours. The SMB share is named
`FileServer`, points to `/Volumes/FileServer/Shared`, and disables guest access.
The `file-server:enable-smb` task enables and starts macOS's built-in SMB
LaunchDaemon if port 445 is closed; it asks for `sudo` only when needed.
Alternatively, enable **File Sharing** in System Settings → General → Sharing
and **Share files and folders using SMB** in its Options. The
status task checks that the SMB service is listening on port 445. Grant access
only to the intended local or sharing-only users. The share
task does not create user accounts or change their passwords.

For password-protected access to ordinary files, create a dedicated
**Sharing Only** user in System Settings → Users & Groups → Add User. Enter its
password in the local macOS dialog and store it in a password manager; never
put it in mise configuration or this repository. In System Settings → General
→ Sharing → File Sharing, select the `Shared` row, add this user under **Users**,
and give it **Read & Write**. Set **Everyone** to **No Access**. In the
`Shared` row's **Advanced Options**, leave guest access off. In Users & Groups
→ Guest User, leave **Allow guest users to connect to shared folders** off.
Do not add this account to the separate `TimeMachine` share unless it is also
intended for backups. Restrict `TimeMachine` to its intended backup account:
the share's **Everyone** permission and the folder's group/other permissions
must not grant this sharing-only account access. [Apple's Sharing Only user
instructions](https://support.apple.com/guide/mac-help/mchlp15577/mac)
describe the account type.

The external RAID must enforce file ownership for user-specific permissions.
Check `diskutil info <mounted-RAID-volume>` for `Owners: Enabled`; if it says
`Disabled`, run `sudo diskutil enableOwnership <mounted-RAID-volume>` once on
the server. macOS retains this setting across reconnects and reboots on that
Mac. Ensure the `Shared` folder grants the sharing-only account inherited
read/write access so files later created by the server owner remain editable.
Verify a client can connect, create, edit, and delete a test file before relying
on the share.

If a client reports that `FileServer` does not exist, first verify the live SMB
name and guest flag with `sharing -l -f json`, then confirm the RAID is mounted,
`Owners` is enabled, and the account has access to the folder. Seeing a share
in the server's list does not prove that an authenticated mount will succeed.
Retry from a second Mac using `smb://<server-host>/FileServer` and **Registered
User**. The `file-server:status` task checks the SMB share flags and listener,
but not the account password, ownership setting, or per-user folder access.

A confirmed cause of external-share connection failures is macOS privacy
protection (TCC) denying `smbd` access to removable volumes in a background
session. The File Sharing panel's **Full Disk Access** switch alone did not
provide the required permission. In System Settings → Privacy & Security →
Full Disk Access, add the built-in `/usr/sbin/smbd` executable and enable it.
The file picker accepts its path through Command-Shift-G. Then restart SMB
with `sudo launchctl kickstart -k system/com.apple.smbd`; this disconnects
existing SMB sessions. Keep guest access disabled and retain the folder ACLs.

Use `/usr/bin/log show --last 10m --style compact --predicate 'process == "tccd" AND eventMessage CONTAINS[c] "smbd"'`
to check for `Refusing TCCAccessRequest` and
`kTCCServiceSystemPolicyRemovableVolumes`. Use the absolute command path:
some shells have a different `log` builtin. A successful `ls -ld` only proves
metadata access, and a failed `sudo -u` write probe can have a different TCC
identity from the SMB service. After granting the permission, verify an actual
authenticated client mount and create/edit/delete a disposable test file over
SMB. Also verify the ordinary sharing account cannot open `TimeMachine`.
Do not treat the server status task alone as proof of client access.

### Access shared files from an iPhone

macOS Sharing shows the folder's row name, while SMB clients use its configured
SMB share name. On this server they map as follows:

| Sharing row | SMB share name | Folder on the RAID | SMB address |
| --- | --- | --- | --- |
| `Shared` | `FileServer` | `/Volumes/FileServer/Shared` | `smb://<server-host>/FileServer` |
| `TimeMachine` | `TimeMachine` | `/Volumes/FileServer/TimeMachine` | `smb://<server-host>/TimeMachine` |

The `TimeMachine` share is reserved for Mac backups.

1. On the home network, connect the iPhone and Mac mini to the same LAN. For
   access away from home, connect Tailscale on both devices and use the Mac
   mini's Tailscale hostname or IP address.
2. On the iPhone, open **Files → Browse → More (⋯) → Connect to Server**.
3. Enter `smb://<server-host>/FileServer` and tap **Connect**. Use the Mac
   mini's local hostname on the LAN, or its Tailscale hostname or IP when
   using Tailscale. Do not enter the on-disk `Shared` path as the share name.
4. Choose **Registered User**, enter the dedicated Sharing Only user's name
   and password, and tap **Next**. Open the connected
   `FileServer` share from the Files sidebar. Guest access is disabled.

If the connection fails, check that the Mac mini has been unlocked after a
FileVault reboot, File Sharing and SMB are enabled, and both devices can reach
each other on the chosen network. See [Apple's Files instructions](https://support.apple.com/ja-jp/guide/iphone/iphe9aff429a/ios)
and [Tailscale's SMB guide](https://tailscale.com/docs/use-cases/personal-or-at-home-use/access-nas-media-file-servers).
Keep the actual tailnet name and IP address out of this repository and mise
configuration.

Repository initialization generates a strong encryption password, saves it in the user's
Keychain for interactive use, and writes a mode-600 recovery copy to
`~/.config/mise-darwin/file-server-recovery-password.txt`. Copy that password
to a password manager or another safe place off this Mac; it is essential for
recovery on another Mac. Never commit the recovery file. The scheduled agent
reads that owner-only file to avoid a Keychain prompt. It runs in the user's logged-in
GUI session, so after a FileVault-protected reboot, unlock the Mac before
expecting scheduled backups or Tailscale access.

macOS may separately require removable-volume access for the backup LaunchAgent.
After loading it, trigger and inspect one background run:

```sh
launchctl kickstart -k gui/$(id -u)/dev.mise.file-server-backup
launchctl print gui/$(id -u)/dev.mise.file-server-backup
```

Approve any local external-storage access dialog and confirm the job's last exit
code is 0. If macOS denies access, review [external-storage app access](https://support.apple.com/guide/mac-help/allow-use-of-external-and-removable-storage-mchl6f613f75/mac)
under System Settings → General → Storage and retry. A foreground backup does not prove that launchd
has access to the external volume.

The recovery check runs at login and every five minutes. Once the enrolled RAID
and backup volumes are mounted, it creates a Kopia snapshot if the latest one
is missing, failed, or at least 24 hours old. It separately checks the private
`FileServer` and `TimeMachine` share records, the Time Machine destination
option, SMB listener, and RAID members,
so an SMB fault does not prevent a due backup. The daily 03:00 job
remains as an independent scheduled backup. Both jobs share a lock, so they do
not write to the repository concurrently. A missing or replaced volume causes
an explicit error and **never** creates a backup under an empty mount point.
macOS retains the enabled SMB service and share records across restarts; the
check reports any drift that needs administrator repair. After a cold reboot,
FileVault must be unlocked and this user must log in before the recovery agent
can start. A detached or powered-off HDD must reconnect and mount before
the next check can use it. Inspect the agent with:

```sh
launchctl print gui/$(id -u)/dev.mise.file-server-reconcile
tail -n 30 ~/Library/Logs/mise-file-server-reconcile.err.log
```

To recover, connect the backup disk to a Mac with Kopia, [connect to the existing
repository](https://kopia.io/docs/reference/command-line/) at `/Volumes/FileServerBackup/Kopia` with the saved password, then
use `kopia snapshot list` and [restore](https://kopia.io/docs/reference/command-line/common/snapshot-restore/) with
`kopia snapshot restore <snapshot-id> <new-target>`.
Restore to a new empty directory first and inspect the files before replacing
live data. Recreate the AppleRAID mirror separately if it was lost; Kopia
restores files and history, not the RAID layout. Test a sample-file restore
after the first backup and periodically thereafter. RAID protects availability
against one disk failure; the separate versioned backup protects against
deletion and overwrite. Important data still needs an off-site copy.

### Time Machine destination for other Macs

Keep Time Machine images separate from ordinary SMB files. On the server, run
`mise -E macos-arm64 -E desktop -E file-server run file-server:time-machine-share`
to create `/Volumes/FileServer/TimeMachine` on the enrolled RAID mirror and
register a private SMB share named `TimeMachine`. The ordinary files remain in
`/Volumes/FileServer/Shared` (`smb://<server>/FileServer`). Repeating the task
preserves existing backup images and the share record.

In System Settings → General → Sharing, turn on File Sharing and SMB. Click the
info button next to File Sharing; if its **Options** sheet is open, click
**Done** to return to the Shared Folders list. Control-click (or right-click)
the `TimeMachine` row in that list and choose **Advanced Options** from its
context menu. Turn on
**Share as a Time Machine backup destination**. Set **Limit backups to** to
**2,000 GB (2 TB) on the shared destination for the two client Macs**, rather than
configuring 2 TB separately on each client. [Apple's Time Machine sharing
instructions](https://support.apple.com/ja-jp/guide/mac-help/mchl31533145/mac)
describe these controls. Multiple
Macs can use the same destination; each creates its own backup image. The
server's Kopia job backs up only `Shared`, not the live Time Machine images.

On **each** client Mac:

1. Connect to the Mac mini on the same LAN, or connect both Macs to the same
   tailnet if using Tailscale. The Mac mini must be awake, logged in after
   any FileVault reboot, and have the RAID volume mounted.
2. Open **System Settings → General → Time Machine → Add Backup Disk** (or the
   **+** button if a destination is already configured). Select the
   `TimeMachine` network destination, then click **Set Up Disk**.
3. When asked to connect, use an SMB-enabled account on the Mac mini with
   access to the `TimeMachine` share. Enable **Encrypt Backups** and save the
   separate backup-encryption password somewhere safe. Complete setup and
   confirm the first backup finishes before setting up the second Mac.

If `TimeMachine` does not appear in the destination list, in Finder choose
**Go → Connect to Server** (Command-K) and mount
`smb://<server-host>/TimeMachine` with that account, then return to Time
Machine settings. Use the Mac mini's local hostname on the LAN or its
Tailscale hostname or IP over Tailscale. A direct SMB mount can make a network
destination selectable when automatic discovery does not find it; see [Apple's
supported-disk guidance](https://support.apple.com/ja-jp/guide/mac-help/mh15139/mac).
If it still does not appear, check the server's **Share as a Time Machine backup
destination** option and the client's SMB connection. [Apple's client setup
instructions](https://support.apple.com/ja-jp/guide/mac-help/mh11421/mac)
describe the Time Machine controls. An ordinary SMB share record alone does
not prove the Time Machine destination option is enabled.

## Current boundaries

- mise bootstrap converges sequentially and does not provide nix-darwin atomic
  switches or generation rollback.
- mise manages formulae directly. Homebrew Bundle handles casks and third-party
  taps without API metadata, avoiding conflicts with existing cask receipts.
- Mac App Store installation may require an Apple Account. Xcode may require a
  first launch and license acceptance.
- Add a dedicated, reviewable task and plist when a system LaunchDaemon becomes
  necessary.
