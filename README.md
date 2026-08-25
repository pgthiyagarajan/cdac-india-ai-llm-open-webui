# BharatAI Platform — Changes Summary

This document lists everything that was added, changed, or created on top of
the base Open WebUI codebase for the BharatAI Platform. It's an index only —
no code is shown here. For exact code, diffs, root-cause explanations, and
verification steps for every item below, see **[PROJECT_CHANGES.md](PROJECT_CHANGES.md)**,
which is organized into the same numbered sections referenced throughout this
file.

## What was built, in plain terms

1. **New authentication control flow.** Public "Sign in" no longer offers a
   local email/password form at all — it goes straight to Parichay SSO.
   "Sign up" shows a direct registration form (extended with
   Department/Designation/Mobile Number fields). New accounts (both SSO and
   direct signup) land as `pending` until an admin approves them.
2. **`/staff` — an internal, unlinked sign-in page** for the organization's
   own employees and the bootstrap admin account, using local
   email/password/LDAP credentials. Not linked from anywhere in the public
   UI — reachable only by typing the URL directly.
3. **Registration & Sign-in Guide** — a PDF user manual for the sign-up
   process, opened from the landing page and the sign-up page, sliding in as
   a side panel next to the sign-up form.
4. **In-app User Guide** — a second PDF user manual for the platform itself,
   opened from a button in the main chat UI, sliding in as a real
   split-screen panel (not an overlay) next to the chat/sidebar.
5. **"Submit Feedback" moved from the public landing page into the app**,
   reachable from the logged-in user's account menu (both the top-right and
   bottom-left instances of it). The email field was removed — it's now
   filled automatically from the logged-in user's account, nothing typed by
   hand.

## Full file list

Paths are relative to the project root.

### 1. Auth control flow + `/staff`

**Backend**
- `backend/open_webui/models/auths.py`
- `backend/open_webui/models/users.py`
- `backend/open_webui/routers/auths.py`
- `backend/open_webui/routers/users.py`
- `backend/open_webui/utils/oauth.py`
- `backend/open_webui/migrations/versions/d5e6f7a8b9c0_add_department_designation_mobile_to_user_table.py` — new file

**Frontend**
- `src/routes/auth/+page.svelte`
- `src/routes/staff/+page.svelte` — new file
- `src/routes/+layout.svelte`
- `static/homepage.html`
- `src/lib/apis/auths/index.ts`
- `src/lib/apis/users/index.ts`
- `src/lib/components/admin/Users/UserList/EditUserModal.svelte`
- `src/lib/i18n/locales/en-US/translation.json`
- `static/parichay-logo.png` — new file

### 2. Registration & Sign-in Guide (sign-up user manual)

- `static/homepage.html` (trigger link + slide-in panel on the landing page)
- `src/routes/auth/+page.svelte` (same panel on the sign-up page)
- `static/static/user-manual-signin.pdf` — new file, the PDF itself

### 3. In-app User Guide (platform user manual)

- `src/lib/components/layout/AppGuidePanel.svelte` — new file, the panel itself
- `src/lib/components/icons/UserManualBook.svelte` — new file, its trigger icon
- `src/lib/components/chat/Navbar.svelte` (trigger button)
- `src/routes/(app)/+layout.svelte` (real layout split, not an overlay)
- `src/lib/components/layout/Sidebar.svelte` (drag-to-resize fix, needed for this feature to coexist with the sidebar's own resize handle)
- `src/lib/stores/index.ts` (shared open/close + resize-width state)
- `static/static/user-manual-app.pdf` — new file, the PDF itself

### 4. "Submit Feedback" relocation

- `src/lib/components/layout/Sidebar/SubmitFeedbackModal.svelte` — new file, the modal itself
- `src/lib/components/layout/Sidebar/UserMenu.svelte` (menu entry, in both the top-right and bottom-left instances)
- `static/homepage.html` (old button/modal/script removed)
- `backend/open_webui/main.py` (feedback-save bug fix — see below)

### Documentation

- `PROJECT_CHANGES.md` — new file, the full technical changelog this file points to

## Replacing the placeholder PDFs

Both user manuals currently ship as placeholder/dummy PDFs. They are
referenced by a **fixed file path only** — nowhere in the code is the PDF
generated, templated, or referenced by anything other than that path — so
replacing them is a **file-only operation**, no code changes needed:

| Manual | Replace this file |
|---|---|
| Registration & Sign-in Guide (landing page + sign-up page) | `static/static/user-manual-signin.pdf` |
| In-app platform User Guide | `static/static/user-manual-app.pdf` |

Keep the exact same filenames and the exact same folder
(`static/static/`, not `static/` — see `PROJECT_CHANGES.md` §2.4.1 for why
that nested path matters on this project's dev server) and every page that
opens these PDFs will pick up the new file automatically.

## Where to go for detail

Every item above has a corresponding numbered section in
**[PROJECT_CHANGES.md](PROJECT_CHANGES.md)** with the actual code, the
reasoning behind each decision, every bug that came up along the way (and
its root cause), and a verification checklist. This file is only the index.
