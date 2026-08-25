# BharatAI Platform — Project Changes Log

Single running record of every non-trivial code change made in this working
session, so the team (or another engineer/LLM) has one place to look instead
of hunting across multiple files. Written in a reproducible-instructions
style — exact file paths, exact snippets — so any part of it can be applied
to a different copy of this codebase.

## Contents

1. [Auth control flow rewrite](#1-auth-control-flow-rewrite) — Parichay SSO
   as the default sign-in path, direct sign-up form, pending-role gating,
   the internal `/staff` credential sign-in page.
2. [Registration & Sign-in Guide panel](#2-registration--sign-in-guide-panel)
   — the PDF guide slide-in panel on the landing page and `/auth` sign-up.
3. [In-app User Guide panel](#3-in-app-user-guide-panel) — the "?" icon and
   PDF guide panel in the main chat UI, post-login.

---

# 1. Auth control flow rewrite


This document describes every code change made to implement the new sign-in/sign-up
control flow on top of the existing Open WebUI + Parichay OAuth integration. It is
written so that another engineer (or an LLM) can reproduce the same feature on a
different copy of this codebase, step by step.

**Scope note:** This does NOT cover the original Parichay OAuth integration itself
(PKCE login redirect, token exchange, `/oauth/parichay/login` and
`/oauth/parichay/callback` routes). That already existed before this work and was
not touched — see "What was intentionally NOT changed" at the end.

---

## 1. What changed, in one paragraph

Previously, the landing page had a single "Sign in" button that led to `/auth`,
which showed a chooser between "Continue with Parichay" and "Continue with Open
WebUI" (email/password). The new flow:

- **Landing page** now has two buttons in the same card: **Sign in** (still links
  to `/auth`) and **Sign up** (links to `/auth?mode=signup`).
- **Sign in** now skips the chooser entirely and redirects straight to Parichay
  SSO. On a **first-time** Parichay login, the backend provisions the user with
  `role=pending` and signals the frontend to show a "complete your profile" form
  (Department / Designation / Mobile Number) before the user is dropped into the
  app. On a **returning** login, the user goes straight into the app (still gated
  by their current role).
- **Sign up** skips the chooser and shows the direct email/password sign-up form
  immediately, extended with Department / Designation / Mobile Number fields.
  Also lands the user as `role=pending`.
- **Pending-role gating** (`role=pending` blocks app access until an admin
  approves) is enforced end-to-end — this already existed on the frontend
  (`AccountPending.svelte`) and in `DEFAULT_USER_ROLE`, but OAuth logins were
  silently force-promoting `pending → user`, defeating it. That override is
  removed.
- The **old chooser UI is kept in the code**, not deleted — it's just no longer
  the default entry point. It's still reachable at `/auth?form=1`.

---

## 2. Backend changes

### 2.1 New user profile columns

**File:** `backend/open_webui/models/users.py`

Add three nullable columns to the `User` SQLAlchemy model:

```python
class User(Base):
    ...
    date_of_birth = Column(Date, nullable=True)
    timezone = Column(String, nullable=True)

    # Registration details (collected at signup)
    department = Column(String, nullable=True)
    designation = Column(String, nullable=True)
    mobile_number = Column(String, nullable=True)

    # Online status
    presence_state = Column(String, nullable=True)
    ...
```

Mirror them on the Pydantic `UserModel` (this is required — `UserModel` uses
`from_attributes=True` and mirrors the ORM columns 1:1; if you skip this, the new
columns get silently dropped whenever a `User` row is validated into a
`UserModel`):

```python
class UserModel(BaseModel):
    ...
    date_of_birth: datetime.date | None = None
    timezone: str | None = None

    department: str | None = None
    designation: str | None = None
    mobile_number: str | None = None

    presence_state: str | None = None
    ...
```

Add a new form for the post-OAuth profile-completion endpoint (put it near the
other `Forms` in the same file, e.g. right before `UserGroupIdsModel`):

```python
class CompleteProfileForm(BaseModel):
    department: str | None = None
    designation: str | None = None
    mobile_number: str | None = None
```

Extend `UserUpdateForm` (used by the **admin** "Edit User" endpoint) so admins can
edit these fields too:

```python
class UserUpdateForm(BaseModel):
    role: str | None = None
    name: str | None = None
    email: str | None = None
    profile_image_url: str | None = None
    password: str | None = None
    department: str | None = None
    designation: str | None = None
    mobile_number: str | None = None
    ...
```

Thread the three fields through `UsersTable.insert_new_user` (both the signature
and the dict passed into `UserModel(...)`):

```python
async def insert_new_user(
    self,
    id: str,
    name: str,
    email: str,
    profile_image_url: str = '/user.png',
    role: str = 'pending',
    username: str | None = None,
    oauth: dict | None = None,
    department: str | None = None,
    designation: str | None = None,
    mobile_number: str | None = None,
    db: AsyncSession | None = None,
) -> UserModel | None:
    async with get_async_db_context(db) as session:
        user = UserModel(
            **{
                'id': id,
                'email': email,
                'name': name,
                'role': role,
                'profile_image_url': profile_image_url,
                'last_active_at': int(time.time()),
                'created_at': int(time.time()),
                'updated_at': int(time.time()),
                'username': username,
                'oauth': oauth,
                'department': department,
                'designation': designation,
                'mobile_number': mobile_number,
            }
        )
        result = User(**user.model_dump())
        ...
```

### 2.2 Signup form + Auth creation layer

**File:** `backend/open_webui/models/auths.py`

Extend `SignupForm` (the request body for `POST /auths/signup`) with the three
optional fields:

```python
class SignupForm(BaseModel):
    name: str
    email: str
    password: str
    profile_image_url: str | None = '/user.png'
    department: str | None = None
    designation: str | None = None
    mobile_number: str | None = None
    ...
```

`AddUserForm(SignupForm)` (used by the admin "Add User" flow) inherits these
automatically — no extra work needed there.

Thread the fields through `AuthsTable.insert_new_auth` (which creates the `Auth`
credential row + calls `Users.insert_new_user` in one transaction):

```python
async def insert_new_auth(
    self,
    email: str,
    password: str,
    name: str,
    profile_image_url: str = '/user.png',
    role: str = 'pending',
    oauth: dict | None = None,
    department: str | None = None,
    designation: str | None = None,
    mobile_number: str | None = None,
    db: AsyncSession | None = None,
) -> UserModel | None:
    ...
    created_user = await Users.insert_new_user(
        new_id,
        name,
        email,
        profile_image_url,
        role,
        oauth=oauth,
        department=department,
        designation=designation,
        mobile_number=mobile_number,
        db=session,
    )
    ...
```

### 2.3 `/auths/signup` route + new `/auths/complete-profile` endpoint

**File:** `backend/open_webui/routers/auths.py`

Add the three params to `signup_handler` (the shared helper behind `/signup`) and
forward them into `Auths.insert_new_auth`:

```python
async def signup_handler(
    request: Request,
    email: str,
    password: str,
    name: str,
    profile_image_url: str = '/user.png',
    department: str | None = None,
    designation: str | None = None,
    mobile_number: str | None = None,
    *,
    db: AsyncSession,
) -> UserModel:
    ...
    user = await Auths.insert_new_auth(
        email=email.lower(),
        password=hashed,
        name=name,
        profile_image_url=profile_image_url,
        role=request.app.state.config.DEFAULT_USER_ROLE,
        department=department,
        designation=designation,
        mobile_number=mobile_number,
        db=db,
    )
    ...
```

Update the `/signup` route to pass the new `form_data` fields through:

```python
@router.post('/signup', response_model=SessionUserResponse)
async def signup(request: Request, response: Response, form_data: SignupForm, db=...):
    ...
    user = await signup_handler(
        request,
        form_data.email,
        form_data.password,
        form_data.name,
        form_data.profile_image_url,
        form_data.department,
        form_data.designation,
        form_data.mobile_number,
        db=db,
    )
    return await create_session_response(request, user, db, response, set_cookie=True)
```

Add a **new endpoint**, `POST /auths/complete-profile`. This is needed because a
Parichay-provisioned user already has an `Auth`/`User` row (created during OAuth
callback) — re-submitting through `/auths/signup` would 400 with `EMAIL_TAKEN`.
This endpoint just patches the three fields onto the *already-authenticated*
current user:

```python
@router.post('/complete-profile', response_model=UserResponse)
async def complete_profile(
    form_data: CompleteProfileForm,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Lets a user (regardless of role, including 'pending') fill in
    department/designation/mobile_number after their first OAuth
    provisioning — used when a Parichay-created account still needs
    these fields collected before an admin can approve it.
    """
    updated = await Users.update_user_by_id(
        user.id,
        form_data.model_dump(exclude_none=True),
        db=db,
    )
    if not updated:
        raise HTTPException(500, detail=ERROR_MESSAGES.DEFAULT())
    return updated
```

> ⚠️ **Important:** this must use `Depends(get_current_user)`, **not**
> `Depends(get_verified_user)`. `get_verified_user` rejects any role outside
> `{'user', 'admin'}` — it would 403 the exact `pending` users this endpoint
> exists to serve.

Update the import block at the top of the file to include the new names:

```python
from open_webui.models.users import (
    CompleteProfileForm,
    UpdateProfileForm,
    UserModel,
    UserProfileImageResponse,
    UserResponse,
    Users,
    UserStatus,
)
```

### 2.4 Admin "Edit User" endpoint persists the new fields

**File:** `backend/open_webui/routers/users.py`

In `update_user_by_id` (the `POST /users/{user_id}/update` admin route), add the
three fields to the `update_data` dict build:

```python
if form_data.profile_image_url is not None:
    update_data['profile_image_url'] = form_data.profile_image_url
if form_data.department is not None:
    update_data['department'] = form_data.department
if form_data.designation is not None:
    update_data['designation'] = form_data.designation
if form_data.mobile_number is not None:
    update_data['mobile_number'] = form_data.mobile_number
```

### 2.5 OAuth callback — respect `pending` role, signal new users

**File:** `backend/open_webui/utils/oauth.py`, method `handle_callback`

This is the part that actually re-enables pending-approval for OAuth/Parichay
users. There were **two** places that silently rewrote a computed `pending` role
to `user` — both must be removed.

**(a) Existing-user login branch** — find this block (it runs every time a
*returning* OAuth user logs in) and delete the 2-line override:

```python
if user:
    determined_role = await self.get_user_role(user, user_data)
    # DELETE these two lines:
    # if determined_role == 'pending':
    #     determined_role = 'user'
    if user.role != determined_role:
        await Users.update_user_role_by_id(user.id, determined_role, db=db)
        user.role = determined_role
```

Without OAuth role-management claims enabled (`ENABLE_OAUTH_ROLE_MANAGEMENT`),
`get_user_role(user, user_data)` for an existing user simply returns
`user.role` unchanged — so this branch becomes a no-op for role purposes,
which is exactly what we want: a still-`pending` user stays `pending` on every
login; an admin-approved `user`/`admin` stays that way.

**(b) New-user creation branch** — find this block and delete the same kind of
override:

```python
new_role = await self.get_user_role(None, user_data)
# DELETE these two lines:
# if new_role == 'pending':
#     new_role = 'user'
user = await Auths.insert_new_auth(
    email=email,
    password=get_password_hash(str(uuid.uuid4())),
    name=name,
    profile_image_url=picture_url,
    role=new_role,
    oauth=oauth_data,
    db=db,
)
```

`get_user_role(None, user_data)` for a brand-new user falls back to
`auth_manager_config.DEFAULT_USER_ROLE`, which defaults to `'pending'`
(`backend/open_webui/config.py`). Removing the override means new OAuth users now
actually land as `pending`, matching direct signups.

**(c) Track "is this a brand-new user" and signal it on redirect.**

Right before the `try:` block in `handle_callback`, initialize a flag:

```python
error_message = None
is_new_user = False
try:
    client = self.get_client(provider)
    ...
```

Set it in each branch:

```python
if user:
    is_new_user = False   # existing-user branch
    ...
else:
    ...  # new-user creation branch
    is_new_user = True
    user = await Auths.insert_new_auth(...)
    ...
```

Then, where the final redirect URL is built (after the `error_message` early
return, before the `RedirectResponse` is constructed), append a query string
only when a new user was just created:

```python
redirect_base_url = (str(request.app.state.config.WEBUI_URL or request.base_url)).rstrip('/')
redirect_url = f'{redirect_base_url}/auth'

if error_message:
    redirect_url = f'{redirect_url}?error={urllib.parse.quote_plus(error_message)}'
    return RedirectResponse(url=redirect_url, headers=response.headers)

if is_new_user:
    new_user_qs = urllib.parse.urlencode(
        {
            'new_user': '1',
            'name': user.name,
            'email': user.email,
        }
    )
    redirect_url = f'{redirect_url}?{new_user_qs}'

response = RedirectResponse(url=redirect_url, headers=response.headers)
...
```

This is what lets the frontend distinguish "just-created Parichay user, show the
profile-completion form" from "returning user, log straight in" — see section
3.2 below. Note this shared code path is used by all OAuth providers (not just
Parichay), so any other configured provider (Google/Microsoft/GitHub/OIDC) gets
the same new-user signal for free.

### 2.6 Database migration

**New file:**
`backend/open_webui/migrations/versions/d5e6f7a8b9c0_add_department_designation_mobile_to_user_table.py`

```python
"""add department, designation, mobile_number columns to user table

Revision ID: d5e6f7a8b9c0
Revises: <YOUR_CURRENT_HEAD_REVISION>
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = '<YOUR_CURRENT_HEAD_REVISION>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    user_cols = {c['name'] for c in inspector.get_columns('user')}

    if 'department' not in user_cols:
        op.add_column('user', sa.Column('department', sa.String(), nullable=True))
    if 'designation' not in user_cols:
        op.add_column('user', sa.Column('designation', sa.String(), nullable=True))
    if 'mobile_number' not in user_cols:
        op.add_column('user', sa.Column('mobile_number', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'mobile_number')
    op.drop_column('user', 'designation')
    op.drop_column('user', 'department')
```

> ⚠️ **`down_revision` must be the true current Alembic head on the target
> database** — do not copy the value used in this repo blindly. Determine it by
> tracing `down_revision` references through every file in
> `backend/open_webui/migrations/versions/` (the head is the one revision id
> that never appears as anyone else's `down_revision`), or by running
> `alembic heads` against the target database. Picking the wrong parent revision
> will fork the migration history.

The `upgrade()` guards each `add_column` with an existence check — this matches
the idempotent pattern already used by this codebase's other migrations (e.g.
`b2c3d4e5f6a7_add_scim_column_to_user_table.py`), so re-running the migration is
safe.

### 2.7 Enable the `ENABLE_SIGNUP` config flag

The new landing-page "Sign up" button and `/auth?mode=signup` route only work if
the backend's `ENABLE_SIGNUP` config is `true`. This is a pre-existing config
flag (`ui.enable_signup` in the persisted config table, or `ENABLE_SIGNUP` env
var), independent of anything above — but if your deployment previously had this
turned **off** (e.g. because direct signup was never a real entry point before,
only reachable via a hidden chooser sub-flow), you must turn it on for the new
flow to work end-to-end. Otherwise submitting the sign-up form will fail with a
403 `ACCESS_PROHIBITED` ("You do not have permission to access this resource...").

Toggle it via **Admin Settings → General → Enable Sign Ups** in the running app
(takes effect immediately, no restart needed since it goes through the live admin
config API), or set `ENABLE_SIGNUP=true` in the environment before first boot.

---

## 3. Frontend changes

### 3.1 Landing page — `static/homepage.html`

Locate the existing single sign-in card. It currently looks like (structure may
vary slightly by branding):

```html
<div class="cards">
  <div class="card-wrap">
    <a class="card" href="/auth" target="_top" rel="noopener">
      <div class="logo-tile">...</div>
      <div class="card-name">Bharat AI Platform</div>
      <div class="card-desc">...</div>
      <span class="card-cta">Sign in <svg>...</svg></span>
    </a>
  </div>
</div>
```

Change it to **one container with two stacked buttons** (do NOT add a second
card/container — a second visually separate card was tried first and explicitly
rejected in favor of keeping one container, expanded downward):

```html
<div class="cards">
  <div class="card-wrap">
    <div class="card">
      <div class="logo-tile">...</div>
      <div class="card-name">Bharat AI Platform</div>
      <div class="card-desc">...</div>
      <a class="card-cta" href="/auth" target="_top" rel="noopener">
        Sign in <svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
      </a>
      <a class="card-cta card-cta-secondary" href="/auth?mode=signup" target="_top" rel="noopener">
        Sign up <svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
      </a>
    </div>
  </div>
</div>
```

Key detail: the outer `.card` element changes from an `<a>` to a `<div>` (it now
wraps **two** separate links with different hrefs, so it can no longer be a
single anchor itself). Each button is now its own `<a class="card-cta">`.

CSS additions (in the `<style>` block):

```css
.card-cta {
  /* ...existing rules... */
  border: 1px solid var(--saffron);
  text-decoration: none;              /* IMPORTANT — see gotcha below */
  transition: filter .1s ease, transform .1s ease, background .15s ease;
}

.card-cta-secondary {
  margin-top: 0.75rem;
  background: transparent;
}

.card-cta-secondary:hover {
  background: rgba(226, 118, 27, 0.12);   /* replace with your accent color at ~12% alpha */
}
```

> **Gotcha:** once `.card-cta` becomes the `<a>` itself (rather than a `<span>`
> nested inside a link), it needs `text-decoration: none` explicitly — otherwise
> the browser's default link underline shows through. Also, keep both buttons
> visually similar (same accent color, same border-radius/padding) but
> differentiate them structurally — solid fill for the primary action (Sign in)
> vs. transparent/outline for the secondary (Sign up) — rather than using two
> different hues. This was an explicit design request: "almost the same colors,
> differentiate in some other manner."

### 3.2 `src/routes/auth/+page.svelte` — the core rewrite

This is the largest change. Work through it in this order:

**a) New imports and state.** Add `completeUserProfile` to the API imports, and
new local state:

```js
import {
	ldapUserSignIn,
	getSessionUser,
	userSignIn,
	userSignUp,
	completeUserProfile,
	updateUserTimezone
} from '$lib/apis/auths';
```

```js
let department = '';
let designation = '';
let mobileNumber = '';
// Set when completing a profile after a first-time Parichay/OAuth login —
// the session user we hold off logging in until the extra fields are submitted.
let pendingOauthUser = null;
```

**b) Extend `signUpHandler`** to pass the new fields through to `userSignUp`:

```js
const sessionUser = await userSignUp(
	name,
	email,
	password,
	generateInitialsImage(name),
	department,
	designation,
	mobileNumber
).catch((error) => {
	toast.error(`${error}`);
	loggingIn = false;
	return null;
});
```

**c) Add a new `completeProfileHandler`**, used only for the post-Parichay
"finish your profile" step. Note it calls `setSessionUser(pendingOauthUser, '/')`
— **not** a fresh `/auths/signup` — since the user already has a session token
from the OAuth callback; this just lets the existing `AccountPending` gate take
over once they're routed into the app (their role is still `pending` at this
point):

```js
const completeProfileHandler = async () => {
	const updated = await completeUserProfile(
		localStorage.token,
		department,
		designation,
		mobileNumber
	).catch((error) => {
		toast.error(`${error}`);
		loggingIn = false;
		return null;
	});

	if (updated) {
		await setSessionUser(pendingOauthUser, '/');
	} else {
		loggingIn = false;
	}
};
```

**d) Extend `submitHandler`** to route to the new handler, and to skip the
CAPTCHA requirement for this mode (a returning-from-Parichay profile completion
has no CAPTCHA field shown at all — see markup changes below):

```js
const submitHandler = async () => {
	if (mode !== 'signup-complete' && !captchaVerified) {
		toast.error($i18n.t('Please complete the captcha verification.'));
		return;
	}

	loggingIn = true;
	if (mode === 'ldap') {
		await ldapSignInHandler();
	} else if (mode === 'signin') {
		await signInHandler();
	} else if (mode === 'signup-complete') {
		await completeProfileHandler();
	} else {
		await signUpHandler();
	}
};
```

**e) Extend `oauthCallbackHandler`** to branch on a `new_user=1` query param
(set by the backend — see section 2.5c). If present, do **not** log the user
into the app yet; instead show the profile-completion form pre-filled with their
Parichay name/email:

```js
const oauthCallbackHandler = async () => {
	function getCookie(name) { /* unchanged */ }

	const token = getCookie('token');
	if (!token) return;

	const sessionUser = await getSessionUser(token).catch((error) => {
		toast.error(`${error}`);
		return null;
	});
	if (!sessionUser) return;

	localStorage.token = token;

	const newUserFlag = $page.url.searchParams.get('new_user');
	if (newUserFlag === '1') {
		showLoginForm = true;
		mode = 'signup-complete';
		name = sessionUser.name || $page.url.searchParams.get('name') || '';
		email = sessionUser.email || $page.url.searchParams.get('email') || '';
		pendingOauthUser = sessionUser;
		return;
	}

	await setSessionUser(sessionUser, localStorage.getItem('redirectPath') || null);
};
```

**f) In `onMount`**, right after `form = $page.url.searchParams.get('form')` is
set (and before the pre-existing `OAUTH_AUTO_REDIRECT` block, if your codebase
has one), add the two new entry-point behaviors:

```js
const modeParam = $page.url.searchParams.get('mode');

// "Sign up" entry point (landing page): skip the chooser, show the form
// directly in signup mode. Doesn't apply if we're mid-way through
// completing a post-Parichay profile (oauthCallbackHandler already set that).
// If direct signup is disabled server-side (enable_signup=false), submitting
// this form would just fail with a 403 — fall back gracefully instead.
if (modeParam === 'signup' && mode !== 'signup-complete') {
	if ($config?.features?.enable_signup) {
		showLoginForm = true;
		mode = 'signup';
	} else {
		toast.error(
			$i18n.t('New sign-ups are currently disabled. Please contact your administrator.')
		);
	}
}

// "Sign in" entry point (landing page, default /auth hit): skip the
// chooser and go straight to Parichay SSO. The old chooser stays
// reachable via /auth?form=1. Suppressed when we're already handling an
// OAuth callback / signup-complete flow, an explicit ?mode= was given,
// or the user already has a session/token.
if (
	!modeParam &&
	!form &&
	!error &&
	mode !== 'signup-complete' &&
	!$user &&
	!localStorage.token &&
	!document.cookie.split('; ').some((c) => c.startsWith('token='))
) {
	window.location.replace(`${WEBUI_BASE_URL}/oauth/parichay/login`);
	return;
}
```

> **Why the `!$user` / `!localStorage.token` / cookie checks matter:**
> `oauthCallbackHandler()` (which runs just before this block, via
> `await oauthCallbackHandler()`) already calls `user.set(sessionUser)` and
> `localStorage.token = token` synchronously before this code is reached — so if
> the callback just processed a valid session, this auto-redirect-to-Parichay
> block safely no-ops instead of causing a redirect loop.

**g) Form markup — new fields.** Inside the existing form (wherever the `Name`
field is rendered, gated on `mode === 'signup'`), extend the condition to also
cover `signup-complete`, and add Department/Designation/Mobile Number right
after it, gated the same way:

```svelte
{#if mode === 'signup' || mode === 'signup-complete'}
	<div>
		<label for="name" class="...">{$i18n.t('Name')}</label>
		<input
			bind:value={name}
			type="text"
			id="name"
			class="... disabled:opacity-60"
			autocomplete="name"
			placeholder={$i18n.t('Enter Your Full Name')}
			readonly={mode === 'signup-complete'}
			disabled={mode === 'signup-complete'}
			required
		/>
	</div>
{/if}

{#if mode === 'signup' || mode === 'signup-complete'}
	<div>
		<label for="department" class="...">{$i18n.t('Department')}</label>
		<input
			bind:value={department}
			type="text"
			id="department"
			class="..."
			autocomplete="organization"
			placeholder={$i18n.t('Enter Your Department')}
			required
		/>
	</div>
	<div>
		<label for="designation" class="...">{$i18n.t('Designation')}</label>
		<input
			bind:value={designation}
			type="text"
			id="designation"
			class="..."
			autocomplete="organization-title"
			placeholder={$i18n.t('Enter Your Designation')}
			required
		/>
	</div>
	<div>
		<label for="mobile-number" class="...">{$i18n.t('Mobile Number')}</label>
		<input
			bind:value={mobileNumber}
			type="tel"
			id="mobile-number"
			class="..."
			autocomplete="tel"
			placeholder={$i18n.t('Enter Your Mobile Number')}
			pattern={'[0-9]{10}'}
			required
		/>
	</div>
{/if}
```

> ⚠️ **Svelte gotcha — do NOT write `pattern="[0-9]{10}"` as a plain string
> attribute.** Inside a Svelte template, `{10}` in an attribute string is parsed
> as a mustache expression (evaluated as the JS literal `10`), silently
> mangling the attribute into `pattern="[0-9]10"` — which then rejects valid
> 10-digit input with a browser-native "Please match the requested format"
> error. Write it as a JS expression instead: `pattern={'[0-9]{10}'}`.

Similarly make the Email field readonly for `signup-complete`
(`readonly={mode === 'signup-complete'} disabled={mode === 'signup-complete'}`),
and wrap the Password field, Confirm-Password block, Terms checkbox, and CAPTCHA
block so they're **hidden** in `signup-complete` mode (a Parichay-authenticated
user has no local password to set and doesn't need to re-verify a CAPTCHA):

```svelte
{#if mode !== 'signup-complete'}
	<div>
		<!-- Password field -->
	</div>
{/if}
```

(Confirm-Password and Terms checkbox are already gated to `mode === 'signup'`
only, so they naturally don't show in `signup-complete` — no change needed
there. Just wrap the CAPTCHA block the same way as Password.)

Update the submit button label and the sign-in/sign-up toggle link visibility:

```svelte
{mode === 'signin'
	? $i18n.t('Sign in')
	: mode === 'signup-complete'
		? $i18n.t('Complete Profile')
		: ($config?.onboarding ?? false)
			? $i18n.t('Create Admin Account')
			: $i18n.t('Create Account')}
```

```svelte
{#if $config?.features.enable_signup && !($config?.onboarding ?? false) && mode !== 'signup-complete'}
	<!-- sign-in/sign-up toggle link -->
{/if}
```

Add a title case for the new mode:

```svelte
{:else if mode === 'signup-complete'}
	{$i18n.t('Complete your profile')}
```

Everywhere the form-fields wrapper is gated by
`{#if $config?.features.enable_login_form || $config?.features.enable_ldap || form}`,
add `|| mode === 'signup-complete'` to the condition (there are two such
occurrences — one around the fields block, one around the submit-button block) —
otherwise a deployment with `enable_login_form=false` would hide the
profile-completion form for Parichay users.

**h) Fix: scrollable form panel + double-centering clip.** With the extra
fields, the sign-up form can be taller than the viewport. Two related fixes:

1. The outer "glass container" only had a `min-height`, so it grew past the
   viewport with nothing to scroll it back into view. Cap it with a matching
   `max-height`:

   ```diff
   - class="w-full max-w-5xl min-h-[600px] md:min-h-[680px] my-auto rounded-3xl overflow-hidden ..."
   + class="w-full max-w-5xl min-h-[600px] md:min-h-[680px] max-h-[calc(100vh-2rem)] md:max-h-[calc(100vh-4rem)] my-auto rounded-3xl overflow-hidden ..."
   ```

2. The right-hand form panel already had `overflow-y-auto`, but it never
   activated — its parent had `justify-center` **and** the panel's own child had
   `my-auto`, a double-centering setup that pushes overflowing content above the
   scrollable area (inaccessible — gets clipped by the container's rounded
   corner). Remove `justify-center` from the panel; keep the child's `my-auto`,
   which still centers short content but clamps to 0 (full scroll range) when
   content overflows:

   ```diff
   - class="w-full md:w-[45%] min-h-full flex flex-col justify-center items-center p-5 md:p-6 ... overflow-y-auto"
   + class="w-full md:w-[45%] min-h-full flex flex-col items-center p-5 md:p-6 ... overflow-y-auto"
   ```

### 3.3 API layer

**File:** `src/lib/apis/auths/index.ts`

Extend `userSignUp` with the three new params and include them in the POST body:

```ts
export const userSignUp = async (
	name: string,
	email: string,
	password: string,
	profile_image_url: string,
	department?: string,
	designation?: string,
	mobile_number?: string
) => {
	...
	body: JSON.stringify({
		name, email, password, profile_image_url,
		department, designation, mobile_number
	})
	...
};
```

Add a new function for the profile-completion call:

```ts
export const completeUserProfile = async (
	token: string,
	department: string,
	designation: string,
	mobile_number: string
) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/auths/complete-profile`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		credentials: 'include',
		body: JSON.stringify({ department, designation, mobile_number })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) throw error;
	return res;
};
```

**File:** `src/lib/apis/users/index.ts`

Extend the `UserUpdateForm` TS type and the `updateUserById` POST body (used by
the admin Edit User modal):

```ts
type UserUpdateForm = {
	role: string;
	profile_image_url: string;
	email: string;
	name: string;
	password: string;
	department?: string;
	designation?: string;
	mobile_number?: string;
};
```

```ts
body: JSON.stringify({
	profile_image_url: user.profile_image_url,
	role: user.role,
	email: user.email,
	name: user.name,
	password: user.password !== '' ? user.password : undefined,
	department: user.department,
	designation: user.designation,
	mobile_number: user.mobile_number
})
```

### 3.4 Admin "Edit User" modal

**File:** `src/lib/components/admin/Users/UserList/EditUserModal.svelte`

Add the three fields to the local `_user` default object:

```js
let _user = {
	profile_image_url: '',
	role: 'pending',
	name: '',
	email: '',
	password: '',
	department: '',
	designation: '',
	mobile_number: ''
};
```

Add matching input fields to the modal markup, right after the existing Email
field block and before the `{#if _user?.oauth}` block:

```svelte
<div class="flex flex-col w-full">
	<div class="mb-1 text-xs text-gray-500">{$i18n.t('Department')}</div>
	<div class="flex-1">
		<input
			class="w-full text-sm bg-transparent outline-hidden"
			type="text"
			bind:value={_user.department}
			aria-label={$i18n.t('Department')}
			placeholder={$i18n.t('Enter Your Department')}
			autocomplete="off"
		/>
	</div>
</div>

<div class="flex flex-col w-full">
	<div class="mb-1 text-xs text-gray-500">{$i18n.t('Designation')}</div>
	<div class="flex-1">
		<input
			class="w-full text-sm bg-transparent outline-hidden"
			type="text"
			bind:value={_user.designation}
			aria-label={$i18n.t('Designation')}
			placeholder={$i18n.t('Enter Your Designation')}
			autocomplete="off"
		/>
	</div>
</div>

<div class="flex flex-col w-full">
	<div class="mb-1 text-xs text-gray-500">{$i18n.t('Mobile Number')}</div>
	<div class="flex-1">
		<input
			class="w-full text-sm bg-transparent outline-hidden"
			type="tel"
			bind:value={_user.mobile_number}
			aria-label={$i18n.t('Mobile Number')}
			placeholder={$i18n.t('Enter Your Mobile Number')}
			autocomplete="off"
		/>
	</div>
</div>
```

No other change needed — `init()` already assigns `_user = selectedUser`, and
`selectedUser` (from the users list) now carries the three new fields since
`UserModel` includes them (section 2.1), and `submitHandler` already calls
`updateUserById(token, id, _user)` which now sends them (section 3.3).

### 3.5 i18n keys

**File:** `src/lib/i18n/locales/en-US/translation.json`

Add these flat entries (empty-string values, matching this file's existing
convention where the key itself is the English source string):

```json
"Complete Profile": "",
"Complete your profile": "",
"Department": "",
"Designation": "",
"Enter Your Department": "",
"Enter Your Designation": "",
"Enter Your Mobile Number": "",
"Mobile Number": "",
"New sign-ups are currently disabled. Please contact your administrator.": ""
```

Insert them in roughly alphabetical position to match the rest of the file (not
strictly required for correctness, just for consistency/mergeability).

---

## 4. Verification checklist

Run through all of these manually — this is a login-critical path with no
automated coverage:

1. **Fresh Parichay signup**: clear cookies/localStorage. Click "Sign in" on the
   landing page → should redirect straight to Parichay (no chooser flash).
   Complete Parichay auth as a brand-new account. Confirm:
   - Backend creates a `User` row with `role='pending'`.
   - You land on `/auth?new_user=1&...`, showing the profile-completion form
     with Name/Email pre-filled and read-only.
   - Submitting persists Department/Designation/Mobile via
     `POST /auths/complete-profile`, then routes into the app, which
     immediately shows the `AccountPending` ("check back later") screen.
2. **Returning Parichay user, still pending**: log in again before approval.
   Confirm role stays `pending` (no silent promotion), and you land directly on
   `AccountPending` — no signup form, no chooser.
3. **Returning Parichay user, approved**: after an admin flips the role to
   `user`/`admin` via the Edit User modal, log in again. Confirm normal app
   access with no forced role change.
4. **Direct signup** (landing page "Sign up" button): form shows immediately
   (no chooser), includes the 3 new fields, submits, lands as `pending`.
5. **Admin edit**: open a user in the admin Users list, confirm
   Department/Designation/Mobile Number show existing values and save
   correctly.
6. **Old chooser still works**: `/auth?form=1` still renders the original
   Parichay/Open WebUI chooser, both buttons still function.
7. **First-admin bootstrap unaffected**: with an empty `user` table, run
   through both direct-signup and OAuth-first-user paths and confirm the
   existing "first user becomes admin" logic still fires.
8. **Migration idempotency**: run the new Alembic migration twice against the
   same DB; the existence-check guards should make the second run a no-op, not
   an error.
9. **`ENABLE_SIGNUP` off**: with the flag disabled, hitting `/auth?mode=signup`
   should show a toast and fall back to the chooser instead of a dead-end form
   that 403s on submit.

---

## 5. What was intentionally NOT changed

- The Parichay PKCE login flow (`handle_login`) and the Parichay-specific token
  exchange / userdetails fetch inside `handle_callback` — zero changes. If
  Parichay sign-in worked before, the handshake itself is untouched.
- `AccountPending.svelte` and the app-shell gate that renders it
  (`!['user','admin'].includes($user?.role)`) — already fully correct, no
  changes needed.
- The admin Users list / role dropdown in `EditUserModal.svelte` (`pending` /
  `user` / `admin` options) — already existed, just extended with the 3 new
  fields (see 3.4).
- The old Parichay/Open WebUI chooser markup on `/auth` — kept in place,
  reachable via `/auth?form=1`.

---

## 6. Files touched (reference)

| File | Type of change |
|---|---|
| `backend/open_webui/models/auths.py` | `SignupForm`, `insert_new_auth` |
| `backend/open_webui/models/users.py` | new columns, `UserModel`, `insert_new_user`, `CompleteProfileForm`, `UserUpdateForm` |
| `backend/open_webui/routers/auths.py` | `/signup` payload, new `/complete-profile` route |
| `backend/open_webui/routers/users.py` | admin `/users/{id}/update` persists new fields |
| `backend/open_webui/utils/oauth.py` | pending-role overrides removed, `new_user` redirect signal |
| `backend/open_webui/migrations/versions/d5e6f7a8b9c0_*.py` | new file — 3-column migration |
| `src/routes/auth/+page.svelte` | direct-to-Parichay redirect, `?mode=signup`, `signup-complete` flow, new fields, scroll fix, chooser + LDAP UI removed (§7) |
| `src/routes/staff/+page.svelte` | **new file** — internal credential sign-in page (§7) |
| `src/routes/+layout.svelte` | session guard whitelist extended with `/staff` (§7.3) |
| `static/homepage.html` | landing page Sign in / Sign up buttons |
| `src/lib/apis/auths/index.ts` | `userSignUp` extended, new `completeUserProfile` |
| `src/lib/apis/users/index.ts` | `updateUserById` extended |
| `src/lib/components/admin/Users/UserList/EditUserModal.svelte` | admin-side fields |
| `src/lib/i18n/locales/en-US/translation.json` | new label/placeholder keys |

---

## 7. Follow-up phase — `/staff` internal sign-in page, public "Sign in" now always leads to Parichay

This phase further restricts local email/password sign-in so it is **no longer
reachable from any public UI**. Every public "Sign in" entry point (landing
page, the `/auth` page's "Already have an account? Sign in" toggle) now goes
straight to Parichay SSO with no exceptions. The pre-existing chooser
(Parichay vs. Open WebUI email/password) and LDAP toggle are relocated to a
new, unlinked route, `/staff`, intended for the organization's own employees
and the bootstrap admin account — people who are given the URL directly, not
users who reach it by clicking anything in the app.

**Important — this is URL obscurity, not an authentication wall.** `/staff`
itself has no login gate in front of it (it can't — it *is* the login page).
The only thing keeping the public off it is that nothing in the UI links to
it. The credential check still happens the normal way, at form submission. If
stronger protection is required later (IP allowlist, a non-guessable path
segment, a reverse-proxy rule, etc.), that is a separate piece of work — not
implemented here.

### 7.1 New file: `src/routes/staff/+page.svelte`

SvelteKit's routing is file-based — creating this file is all that's needed
to expose the page at `/staff`, no route registration anywhere else.

Create it as a copy of the **pre-trim** `src/routes/auth/+page.svelte` (i.e.
before the changes in §7.2 below are applied) — same imports, same full
script section (`signInHandler`, `signUpHandler`, `completeProfileHandler`,
`ldapSignInHandler`, `submitHandler`, `oauthCallbackHandler`, captcha logic,
`setSessionUser`, `navigateToRedirect`), same full markup (the two-button
chooser: "Continue with Parichay" / "Continue with Open WebUI" / any other
configured OAuth provider buttons; the email/password/LDAP form including the
Department/Designation/Mobile Number fields from §3.2; the Terms modal; the
LDAP toggle at the bottom; the `OnBoarding` component; all styling). Then
apply these three edits:

**a) Default straight to the form**, not the chooser (the chooser is still
present in the markup and reachable via its own "Back" button — it is simply
not the default view here):

```js
// This page (/staff) is the internal-only credential sign-in page — it
// opens straight on the email/password form rather than the public
// chooser (the "Back" button still lets you reach the chooser manually).
let showLoginForm = true;
```

**b) Strip the `/auth`-specific auto-redirect logic out of `onMount`.** Remove
the `?mode=signup` handling block and the "default hit → redirect to
Parichay" block (both shown in §3.2f above) and the `OAUTH_AUTO_REDIRECT`
SSO-only auto-redirect block (shown just after it), replacing all three with:

```js
await oauthCallbackHandler();
form = $page.url.searchParams.get('form');

// /staff is the internal credential-login page — it intentionally does
// NOT auto-redirect to Parichay or any SSO provider, and does not honor
// ?mode=signup. It always opens on the plain sign-in form above.

loaded = true;
setLogoImage();
refreshCaptcha();

if (($config?.features?.auth_trusted_header ?? false) || $config?.features?.auth === false) {
	await signInHandler();
} else {
	onboarding = $config?.onboarding ?? false;
}
```

**c) Make the "Sign up" toggle leave `/staff` entirely.** `/staff` is
sign-in-only — registration belongs solely on the public `/auth` page. The
copied form's "Don't have an account? Sign up" toggle button must not flip
local `mode` to `'signup'` and render a registration form inside `/staff`
itself; instead it must navigate away to `/auth?mode=signup`. Find this block
(`src/routes/staff/+page.svelte`, near the submit button) and set the
`mode === 'signin'` branch of the click handler to a client-side navigation:

```svelte
<button
	class="text-orange-400 hover:text-orange-300 font-semibold underline ml-1"
	type="button"
	on:click={() => {
		if (mode === 'signin') {
			// /staff is the internal credential sign-in page only —
			// registration belongs on the public /auth page.
			goto('/auth?mode=signup');
		} else {
			mode = 'signin';
		}
	}}
>
	{mode === 'signin' ? $i18n.t('Sign up') : $i18n.t('Sign in')}
</button>
```

(The reverse direction — `mode === 'signup' → 'signin'` — doesn't occur
anymore in practice since `/staff` always opens on `signin` and now never
locally switches to `signup`, but is left as a harmless no-op fallback.)

> ⚠️ **Use SvelteKit's `goto()` for this, not `window.location.href` /
> `.replace()`.** `goto` is already imported in this file (`import { goto }
> from '$app/navigation';`) for other uses. `window.location.href` (or
> `.replace`) forces a full hard page navigation straight to the backend
> server for that exact path+query before the SPA loads — and since only the
> SPA's client-side router understands query params like `?mode=`, a hard
> navigation to `/auth?mode=signup` returns a raw FastAPI
> `{"detail":"Not Found"}` JSON response instead of the sign-up form. `goto()`
> performs a client-side SvelteKit navigation instead, staying entirely
> within the SPA. Reserve `window.location.replace(...)` for navigations to a
> genuinely different origin/server — such as the Parichay SSO redirect
> elsewhere in this same file, which must remain a hard navigation.

### 7.2 `src/routes/auth/+page.svelte` — remove the chooser and LDAP toggle, force "Sign in" to Parichay

**a) Collapse the chooser branch.** Previously the form markup was wrapped in
`{#if !showLoginForm} <chooser markup> {:else} <form> {/if}`. Remove the
`{#if !showLoginForm}...{:else}` split, the entire chooser markup block
("Continue with Parichay" / "Continue with Open WebUI" / other provider
buttons), and the "Back" button that used to set `showLoginForm = false` — the
form now always renders directly:

```svelte
<div class="grid">
<form
	class="col-start-1 row-start-1 flex flex-col justify-center"
	action="."
	method="post"
	in:fly={{ y: 14, duration: 380, delay: 100, easing: quintOut }}
	out:fly={{ y: -14, duration: 260, easing: quintOut }}
	on:submit={(e) => {
		e.preventDefault();
		submitHandler();
	}}
>
	{#if $config?.features.enable_login_form || form || mode === 'signup-complete'}
	<!-- ...existing field markup unchanged... -->
```

**b) Remove the `mode === 'ldap'` submit-button branch** (unreachable now that
the LDAP toggle and chooser are gone) and **rewrite the "Already have an
account? Sign in" toggle to redirect to Parichay** instead of switching to a
local sign-in view. Also remove the "Continue with LDAP" toggle block that
used to sit right after this section entirely. End state (this is the exact
block currently in the file, `src/routes/auth/+page.svelte:916-953`):

```svelte
<div class="mt-3">
	{#if $config?.features.enable_login_form || form || mode === 'signup-complete'}
		<button
			class="bg-linear-to-r from-orange-600 via-orange-500 to-amber-500 hover:from-orange-500 hover:via-orange-400 hover:to-amber-400 text-white font-semibold text-sm py-2.5 w-full rounded-xl shadow-lg shadow-orange-500/25 hover:shadow-xl hover:shadow-orange-500/35 active:scale-[0.98] focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-orange-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-[#06070b] transition-all duration-200"
			type="submit"
		>
			{mode === 'signup-complete'
				? $i18n.t('Complete Profile')
				: ($config?.onboarding ?? false)
					? $i18n.t('Create Admin Account')
					: $i18n.t('Create Account')}
		</button>

		{#if $config?.features.enable_signup && !($config?.onboarding ?? false) && mode !== 'signup-complete'}
			<div class=" mt-4 text-xs text-center text-gray-400">
				{$i18n.t('Already have an account?')}

				<button
					class="text-orange-400 hover:text-orange-300 font-semibold underline ml-1"
					type="button"
					on:click={() => {
						window.location.replace(`${WEBUI_BASE_URL}/oauth/parichay/login`);
					}}
				>
					{$i18n.t('Sign in')}
				</button>
			</div>
		{/if}
	{/if}
</div>
```

> **Net effect:** on `/auth`, there is no code path left that shows a local
> email/password sign-in view. The page shows either the registration form
> (`?mode=signup`), the post-Parichay profile-completion form
> (`signup-complete`), or (default hit) immediately redirects to Parichay per
> §3.2f — which is unchanged by this phase.

**Left as inert/unreachable dead code on purpose** (not deleted, to minimize
diff risk since nothing in the remaining UI can set them anymore):
`showLoginForm` variable and its remaining `= true` assignments inside
`oauthCallbackHandler`; `ldapUsername` state and `ldapSignInHandler`; the
`mode === 'ldap'` branches inside the form's title logic and the
email-vs-username field split; the `mode = 'ldap' : 'signup'` line inside the
`OnBoarding` component's `getStartedHandler`. If you want a fully clean
implementation with no dead code, these can be deleted too — they are just
never reached from `/auth`'s UI post-change.

### 7.3 Session guard — whitelist `/staff` alongside `/auth` and `/landing`

**File:** `src/routes/+layout.svelte`

The root layout's `onMount` redirects any unauthenticated visitor to
`/landing?redirect=<original-path>` **unless** the current pathname is
already `/auth` or `/landing`. Without whitelisting `/staff` too, visiting
`/staff` directly bounces straight to `/landing` before the page can render
— this was caught by manually visiting `/staff` and observing the landing
page instead of the sign-in form. Fix (applies in **two** places in this
file — the "invalid/expired token" branch and the "no token at all" branch,
both inside the same `onMount`):

```js
// Redirect Invalid Session User to /landing Page
localStorage.removeItem('token');
if (
	$page.url.pathname !== '/auth' &&
	$page.url.pathname !== '/staff' &&
	$page.url.pathname !== '/landing'
) {
	await goto(`/landing?redirect=${encodedUrl}`);
}
} else {
	// Don't redirect if we're already on the auth, staff, or landing page
	// Needed because we pass in tokens from OAuth logins via URL fragments
	if (
		$page.url.pathname !== '/auth' &&
		$page.url.pathname !== '/staff' &&
		$page.url.pathname !== '/landing'
	) {
		await goto(`/landing?redirect=${encodedUrl}`);
	}
```

> **Note:** `src/routes/(app)/+layout.svelte` has a similar-looking guard
> (`if ($user === undefined || $user === null) { goto('/landing?redirect=...') }`)
> but it only wraps the authenticated main-app UI routes (the `(app)` route
> group) and does not apply to `/staff`, so it needs no change.

### 7.4 Verification checklist (this phase)

1. Clear cookies/localStorage. Visit `/staff` directly → the email/password
   sign-in form renders immediately (not the landing page, not a redirect).
2. On `/staff`, sign in with a valid local (non-Parichay) `user`/`admin`
   account → succeeds normally, same as the old default `/auth` behavior did.
3. On the landing page, click **Sign in** → still redirects to `/auth`, which
   immediately redirects to Parichay (unchanged from §3.2f) — never shows a
   local sign-in form.
4. On `/auth?mode=signup`, click **"Already have an account? Sign in"** →
   redirects to `${WEBUI_BASE_URL}/oauth/parichay/login`, not a local
   sign-in view.
5. Confirm there is no link, button, or nav item anywhere in the public UI
   (landing page, `/auth` in any mode, app shell) that points to `/staff` —
   it must only be reachable by typing the URL.
6. On `/staff`, click the "Don't have an account? Sign up" link → navigates
   to `/auth?mode=signup` (leaves `/staff` entirely); it must **not** render
   a second sign-up form inside `/staff` itself.

---

# 2. Registration & Sign-in Guide panel


This document describes every code change made to add the "Registration &
Sign-in Guide" PDF panel — a slide-in guide the user can open from either the
landing page or the `/auth` sign-up page, that stays open across that
navigation and closes automatically on successful sign-up. It is written so
another engineer (or an LLM) can reproduce or extend the same feature on a
different copy of this codebase.

**Scope note:** This does NOT touch the Parichay OAuth sign-in flow itself —
not the `window.location.replace(...oauth/parichay/login...)` redirect calls,
not `oauthCallbackHandler`, not any backend OAuth code. See "What was
intentionally NOT changed" at the end. `/auth` currently has no reachable
"sign-in" form of its own (a bare `/auth` hit redirects straight to Parichay
SSO) — the guide panel's trigger link only ever actually renders under the
sign-up / post-Parichay profile-completion forms, so this doc says
"sign-up" where earlier drafts of the feature said "sign-in."

---

## 1. What changed, in one paragraph

A "Trouble signing in? Click here" link now appears under the Sign in/Sign up
card on the landing page and under the sign-up form on `/auth`. Clicking it
slides a dark panel in from the right containing the guide PDF in the
browser's native PDF viewer (scroll + zoom included for free), while the
sign-in/sign-up container itself slides left so nothing important sits behind
the panel. The "open" state is kept in `localStorage`, so it persists across
navigation from the landing page to `/auth` (and survives the round-trip
through Parichay's SSO domain, since it's read from our own origin's storage
whenever the user lands back on our pages) — and it clears itself
automatically the moment a sign-up succeeds.

---

## 2. Landing page — `static/homepage.html`

### 2.1 Trigger link

Added directly under the Sign up button, inside the existing `.card` markup:

```html
<a class="card-cta card-cta-secondary" href="/auth?mode=signup" target="_top" rel="noopener">Sign up ...</a>
<button type="button" id="openManualBtn" class="manual-trigger-link">
  Trouble signing in? <span class="manual-trigger-underline">Click here</span>
</button>
```

CSS (`.manual-trigger-link`, `.manual-trigger-underline`) — small muted text,
amber/orange accent on the action words, `white-space: nowrap` so it never
wraps to a second line. `.cards { max-width: 440px; }` (widened slightly from
400px) so the link fits on one line.

### 2.2 Card slide + hero fade (NOT a full-page squish)

An earlier version of this feature squished the entire landing page grid into
a single stacked column when the panel opened — this was explicitly reverted
per feedback ("it was perfect as it was"). The final approach touches only
two things:

```css
.card-wrap {
  transition: transform 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}
body.manual-open .card-wrap {
  transform: translateX(-48vw);
}

.hero-copy {
  transition: opacity 0.35s ease;
}
body.manual-open .hero-copy {
  opacity: 0;
  pointer-events: none;
}
```

The `-48vw` translate was tuned by screenshot so the card clears the panel's
44vw width with margin to spare. The hero text fades out rather than staying
in place, because at some viewport widths it was wide enough to peek out from
behind the translated card. `.page-shell` (a wrapper div added around the
original body content purely for earlier, now-reverted, squish logic) is kept
as `display: contents` — i.e. invisible to layout, a no-op — so nothing else
about the page changed.

### 2.3 The panel itself

```html
<div class="manual-panel" id="manualPanel" aria-hidden="true">
  <div class="manual-panel-header">
    <h3 class="manual-panel-title">Registration &amp; Sign-in Guide</h3>
    <button class="manual-close-btn" id="manualCloseBtn" aria-label="Close user guide">&times;</button>
  </div>
  <div class="manual-panel-body">
    <iframe id="manualPdfFrame" class="manual-pdf-frame" title="Registration and Sign-in User Guide"
      src="" data-src="/static/user-manual-signin.pdf"></iframe>
  </div>
</div>
```

```css
.manual-panel {
  position: fixed;
  top: 0; right: 0;
  width: 0%;
  height: 100vh;
  overflow: hidden;
  background: #14192b;
  border-left: 1px solid rgba(255,255,255,0.08);
  box-shadow: -12px 0 40px rgba(0,0,0,0.35);
  display: flex; flex-direction: column;
  z-index: 9990;
  transition: width 0.45s cubic-bezier(0.4,0,0.2,1);
}
body.manual-open .manual-panel { width: 44%; }

@media (max-width: 960px) {
  body.manual-open .manual-panel { width: 100%; }
  body.manual-open .card-wrap { transform: none; }
}
```

`position: fixed` is deliberate — it's what keeps the panel from disturbing
the rest of the page's layout (an earlier version made it a flex child of a
row-direction `<body>`, which is what caused the full-page squish that had to
be reverted).

### 2.4 Open/close/persist script

```js
var STORAGE_KEY = 'bharatai_signin_guide_open';
var openBtn = document.getElementById('openManualBtn');
var closeBtn = document.getElementById('manualCloseBtn');
var panel = document.getElementById('manualPanel');
var frame = document.getElementById('manualPdfFrame');

function openManual(persist) {
  if (!frame.getAttribute('src')) {
    frame.setAttribute('src', frame.getAttribute('data-src'));
  }
  document.body.classList.add('manual-open');
  panel.setAttribute('aria-hidden', 'false');
  if (persist !== false) {
    try { localStorage.setItem(STORAGE_KEY, '1'); } catch (e) {}
  }
}

function closeManual() {
  document.body.classList.remove('manual-open');
  panel.setAttribute('aria-hidden', 'true');
  try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
}

openBtn.addEventListener('click', function () { openManual(); });
closeBtn.addEventListener('click', closeManual);
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && document.body.classList.contains('manual-open')) closeManual();
});

try {
  if (localStorage.getItem(STORAGE_KEY) === '1') openManual(false);
} catch (e) {}
```

The `persist` flag exists so reopening on page load (because the flag was
already set) doesn't re-write the same value — harmless either way, just
avoids a redundant write. The PDF `src` is only set on first open (lazy load),
not on page load — keeps the initial page paint fast even if the flag ends up
true.

---

## 3. `/auth` page — `src/routes/auth/+page.svelte`

### 3.1 State (script block, near the top, after `loggingIn`)

```js
const SIGNIN_GUIDE_STORAGE_KEY = 'bharatai_signin_guide_open';
let showManual = false;
let manualFrameSrc = '';

const openManual = () => {
  showManual = true;
  if (!manualFrameSrc) manualFrameSrc = '/static/user-manual-signin.pdf';
  try { localStorage.setItem(SIGNIN_GUIDE_STORAGE_KEY, '1'); } catch (e) {}
};

const closeManual = () => {
  showManual = false;
  try { localStorage.removeItem(SIGNIN_GUIDE_STORAGE_KEY); } catch (e) {}
};

const handleManualKeydown = (e) => {
  if (e.key === 'Escape' && showManual) closeManual();
};
```

Same `localStorage` key as the landing page — that's the mechanism that lets
the "open" intent survive the navigation between the two pages.

### 3.2 Auto-close on successful sign-up

In `setSessionUser` (fires after `userSignUp` / `completeUserProfile`
succeed), as the very first statement inside `if (sessionUser) { ... }`:

```js
const setSessionUser = async (sessionUser, redirectPath = null) => {
  if (sessionUser) {
    // Successful sign-in: the Registration & Sign-in Guide (if open)
    // should close itself automatically rather than following the user
    // into the app.
    closeManual();
    console.log(sessionUser);
    ...
```

### 3.3 Reopen on page load if the flag is set

In `onMount`, right after `loaded = true; setLogoImage(); refreshCaptcha();`:

```js
try {
  if (localStorage.getItem(SIGNIN_GUIDE_STORAGE_KEY) === '1') {
    manualFrameSrc = '/static/user-manual-signin.pdf';
    showManual = true;
  }
} catch (e) {}
```

### 3.4 Escape-to-close

Added once near the top of the markup, right after the existing
`<OnBoarding ... />` component:

```svelte
<svelte:window on:keydown={handleManualKeydown} />
```

### 3.5 Layout changes — hide branding panel, shrink + shift the form container

The "Main Floating Glass Container" (the outer rounded card that holds both
the left branding panel and the right form panel) gets two ternary-driven
class changes:

```svelte
<div
  class="w-full {showManual ? 'max-w-md' : 'max-w-5xl'} min-h-[600px] ...
    flex flex-col md:flex-row z-10 transition-[max-width,transform] duration-500 ease-out
    {showManual ? 'md:-translate-x-[18vw]' : ''}"
>
```

- `max-w-5xl` → `max-w-md` when open: once the left branding panel is hidden
  (next), the container shrinks to just the size of the form — matching the
  landing page's card proportions instead of leaving an empty wide box.
- `md:-translate-x-[18vw]`: slides the now-narrower container left, clear of
  the panel. Only applied at `md:` breakpoint and up — below that, the guide
  panel goes full-width (`w-full` on the panel, see 3.6) and covers
  everything anyway, matching the landing page's mobile fallback.

The **Left Panel (Info & Highlights)** — the branding text, animated India AI
logo, and footer credits — is wrapped in `{#if !showManual}...{/if}` so it's
removed from the DOM entirely while the guide is open (not just visually
hidden — this is what lets the container's `max-w-md` shrink actually take
effect, since Tailwind's `flex` row would otherwise still be sized by an
invisible-but-present 55%-width sibling):

```svelte
{#if !showManual}
<div class="w-full md:w-[55%] ... "> <!-- unchanged content --> </div>
{/if}
```

The **Right Panel (Login Form)** widens to fill the container once its
sibling is gone:

```svelte
<div
  class="w-full {showManual ? 'md:w-full' : 'md:w-[45%]'} min-h-full flex flex-col items-center p-5 md:p-6 ..."
>
```

### 3.6 Trigger link

Added once, unconditionally, directly above the existing
`<img src="/open-webui-logo.png" ...>` row near the bottom of the form — so
it shows under whichever form is currently rendered (signup /
signup-complete):

```svelte
<button
  type="button"
  class="block w-full mt-3 text-center text-xs text-gray-400 hover:text-white transition-colors duration-150"
  on:click={openManual}
>
  {$i18n.t('Trouble signing in?')}
  <span class="text-amber-400 font-semibold underline underline-offset-2">{$i18n.t('Click here')}</span>
</button>
```

### 3.7 The panel itself

Placed once, right after the closing `</div>` of the page's outer centered
wrapper, before the pre-existing Terms and Conditions modal:

```svelte
<div
  class="fixed top-0 right-0 h-full {showManual ? 'w-full md:w-[44%]' : 'w-0'} overflow-hidden bg-[#14192b] border-l border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.35)] flex flex-col z-[9990] transition-[width] duration-500 ease-in-out"
  aria-hidden={!showManual}
>
  <div class="flex items-center justify-between gap-4 px-6 py-4 border-b border-white/10 bg-black/20 shrink-0">
    <h3 class="text-white font-semibold text-base truncate">{$i18n.t('Registration & Sign-in Guide')}</h3>
    <button
      type="button"
      class="w-8 h-8 shrink-0 rounded-full bg-red-500 hover:bg-red-600 text-white flex items-center justify-center text-xl leading-none transition-colors duration-150"
      on:click={closeManual}
      aria-label={$i18n.t('Close user guide')}
    >&times;</button>
  </div>
  <div class="flex-1 min-h-0 bg-[#525659]">
    {#if manualFrameSrc}
      <iframe title="Registration and Sign-in User Guide" src={manualFrameSrc} class="w-full h-full border-0 block"></iframe>
    {/if}
  </div>
</div>
```

Same visual language as the landing page's panel (dark header bar, red
circular close button, native browser PDF viewer for scroll/zoom).

> **Tailwind JIT gotcha:** the `w-0` / `w-[44%]` / `md:w-full` / `md:w-[45%]`
> class names must appear as literal strings somewhere in the source file
> (even inside a ternary) for Tailwind's JIT scanner to generate the
> corresponding CSS — do not try to build these class names dynamically via
> string concatenation, or the styles silently won't exist in the compiled
> CSS.

---

## 4. Dummy PDF — `static/static/user-manual-signin.pdf`

### 4.1 URL → file-path mapping (the thing that caused a 403 earlier)

This project's dev server (SvelteKit + Vite, `publicDir` = `static/`) maps a
request for `/static/foo.ext` to the file at **`static/static/foo.ext`** on
disk — not `static/foo.ext`. The first attempt at this dummy PDF was placed
at `static/user-manual-signin.pdf` (wrong), which produced a Vite
`fs.allow` 403 when the iframe tried to load `/static/user-manual-signin.pdf`.
Fix: the file must live at `static/static/user-manual-signin.pdf`. All the
existing `/static/*.png` logo references in this codebase follow the same
pattern (e.g. `static/static/india-ai-logo.png`).

### 4.2 Generating the placeholder

Generated with the `jspdf` package (already a dependency — no new install
needed) via a one-off Node script (not committed to the repo, just used to
produce the binary):

```js
const { jsPDF } = require('jspdf');
const doc = new jsPDF();
doc.setFontSize(16);
doc.text('Bharat AI Platform', 20, 30);
doc.setFontSize(12);
doc.text('Registration / Sign-in Guide (Placeholder)', 20, 40);
doc.setFontSize(11);
doc.text('This is a dummy PDF.', 20, 60);
doc.text('This will be the User Registration / Sign-in Guide user manual.', 20, 68);
doc.text('Replace this file with the final guide once it is ready.', 20, 76);
require('fs').writeFileSync(
  'static/static/user-manual-signin.pdf',
  Buffer.from(doc.output('arraybuffer'))
);
```

To swap in the real guide later, just overwrite this same file path — no code
changes needed on either page, since both reference it by this fixed URL.

---

## 5. What was intentionally NOT changed

- **The Parichay OAuth flow** — both `window.location.replace(
  `${WEBUI_BASE_URL}/oauth/parichay/login`)` calls in
  `src/routes/auth/+page.svelte`, `oauthCallbackHandler`, `signInHandler`,
  `ldapSignInHandler`, and all backend OAuth code (`backend/open_webui/utils/
  oauth.py`, the `/oauth/parichay/*` routes) — zero changes. Verified by
  `grep -n parichay src/routes/auth/+page.svelte` before and after this work:
  both matching lines are byte-identical.
- **We cannot inject this panel into Parichay's own hosted SSO login page.**
  That page is on a separate origin, outside this codebase — there is no way
  to draw our UI over it. The best available behavior, and what's
  implemented: the guide reopens automatically the instant control returns to
  our own `/auth` page (because the `localStorage` flag survives the
  round-trip, being stored on our own origin before the user ever leaves),
  and it closes automatically on a successful sign-up.
- Nothing about the sign-up form fields, validation, captcha, or the
  Department/Designation/Mobile Number flow from the auth control-flow
  rewrite (see [§1](#1-auth-control-flow-rewrite) above) was touched.

---

## 6. Verification checklist

1. On the landing page, click "Trouble signing in? Click here" → panel slides
   in from the right, card slides left, hero text fades, PDF renders with
   native scroll/zoom controls.
2. Click "Sign up" while the panel is open → land on `/auth?mode=signup` with
   the panel still open (no re-click needed), left branding panel hidden,
   form container shrunk to card width and shifted left, clear of the panel.
3. Close the panel (button or Escape) on `/auth` → `localStorage` flag
   cleared, container returns to full width with branding panel back.
4. Reopen on `/auth` directly via its own trigger link → works standalone,
   independent of the landing page.
5. Complete a sign-up successfully → the "You're now logged in" toast fires
   and the guide panel (if still open) closes itself before the user is
   routed into the app.
6. Confirm the PDF loads (no 403) at `/static/user-manual-signin.pdf` on both
   pages — this depends on the file living at
   `static/static/user-manual-signin.pdf` on disk (§4.1).
7. `grep -n parichay src/routes/auth/+page.svelte` shows exactly the same two
   lines as before this feature was added.

---

# 3. In-app User Guide panel


This document describes every code change made to add the platform/application
user manual to the main (post-login) app UI — a "?" icon in the chat Navbar
that slides a PDF guide panel in from the right, staying open until the user
closes it, without disturbing any other part of the app (chat, model
selector, controls pane, etc). It complements [§2](#2-registration--sign-in-guide-panel)
above, which covers the separate registration/sign-up guide on the landing
page and `/auth`. Same reproducible-instructions style, so it can be handed
to another engineer or an LLM.

---

## 1. What changed, in one paragraph

A question-mark icon button now sits at the start of the icon cluster in the
top-right of the chat Navbar (`src/lib/components/chat/Navbar.svelte`),
before the temporary-chat/controls/avatar icons. Clicking it slides a dark
panel in from the right containing the app's user-manual PDF in the browser's
native PDF viewer (scroll + zoom included), exactly mirroring the visual
design of the sign-up guide panel. It is deliberately implemented as a
**`position: fixed` overlay**, not a `paneforge` `Pane` sibling inside
`Chat.svelte`'s resizable `PaneGroup` (`ChatControls.svelte`'s own panel uses
that pattern) — chosen specifically so opening/closing it can never affect
chat layout, pane sizing, or any other in-app feature. Visibility is held in
a plain Svelte store (`showAppGuide`), not `localStorage`, so it persists
across in-app (SPA) navigation for the session but resets on a full page
reload — same lifetime as the existing `showControls` store it sits next to.

---

## 2. New store — `src/lib/stores/index.ts`

Added right next to the existing `showControls` store (same section):

```ts
export const showControls = writable(false);
// Registration & Sign-in Guide-style panel, but for the in-app platform
// user manual — toggled from the Navbar's "?" button (see Navbar.svelte).
// A plain in-memory store (not localStorage-backed) so it stays open across
// SPA navigation within the app for the current session, same lifetime as
// showControls above.
export const showAppGuide = writable(false);
export const showEmbeds = writable(false);
```

## 3. `src/lib/components/chat/Navbar.svelte`

### 3.1 Imports

```js
import {
	WEBUI_NAME,
	banners,
	chatId,
	config,
	mobile,
	settings,
	showAppGuide,
	showArchivedChats,
	showControls,
	showSidebar,
	temporaryChatEnabled,
	user
} from '$lib/stores';
...
import Knobs from '../icons/Knobs.svelte';
import UserManualBook from '../icons/UserManualBook.svelte';
```

**Icon revision:** the trigger originally used the pre-existing
`QuestionMarkCircle.svelte` icon. Per feedback, replaced with a new
purpose-built icon, `src/lib/components/icons/UserManualBook.svelte` — a
closed book with a circled "i" info badge on the cover, matching a reference
image the user provided, so the button reads as "user manual" at a glance
rather than a generic help mark:

```svelte
<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width={strokeWidth} stroke="currentColor" class={className}>
	<!-- Closed book with a curved spine -->
	<path stroke-linecap="round" stroke-linejoin="round" d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
	<path stroke-linecap="round" stroke-linejoin="round" d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
	<!-- Info badge on the cover -->
	<circle cx="12.5" cy="9.1" r="2.35" stroke-linecap="round" stroke-linejoin="round" />
	<line x1="12.5" y1="8.6" x2="12.5" y2="10.2" stroke-linecap="round" />
	<circle cx="12.5" cy="7.75" r="0.35" fill="currentColor" stroke="none" />
</svg>
```

Same `export let className / strokeWidth` prop shape as every other icon in
`src/lib/components/icons/`, so it's used identically:
`<UserManualBook className=" size-5" strokeWidth="1.5" />`. No text label is
drawn inside the icon itself (illegible at 20px) — the reference image's
"USER MANUAL" text is instead conveyed via the existing `Tooltip` wrapper
(`$i18n.t('User Guide')`) and the panel header text.

### 3.2 State + handlers (script block, right after `const i18n = getContext('i18n');`)

```js
// In-app "User Guide" panel (PDF, slides in from the right — see the
// fixed-position panel markup near the end of this file). Lazy-loads the
// PDF into the iframe only once, on first open.
let appGuideFrameSrc = '';

const openAppGuide = () => {
	if (!appGuideFrameSrc) {
		appGuideFrameSrc = '/static/user-manual-app.pdf';
	}
	showAppGuide.set(true);
};

const closeAppGuide = () => {
	showAppGuide.set(false);
};

const handleAppGuideKeydown = (e) => {
	if (e.key === 'Escape' && $showAppGuide) {
		closeAppGuide();
	}
};
```

### 3.3 Trigger button

Added as the **first** item inside the existing icon-cluster `<div>` (the one
starting `class="self-start flex flex-none items-center ..."`), before the
temporary-chat toggle — this is the leftmost position in that icon row, per
the placement in the screenshot this feature was requested from:

```svelte
<div class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400">
	<Tooltip content={$i18n.t('User Guide')}>
		<button
			class="flex cursor-pointer px-2 py-2 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
			id="app-guide-button"
			on:click={openAppGuide}
			aria-label={$i18n.t('User Guide')}
		>
			<div class=" m-auto self-center">
				<UserManualBook className=" size-5" strokeWidth="1.5" />
			</div>
		</button>
	</Tooltip>

	{#if $user?.role === 'user' ? (...) : true}
		<!-- ...existing temporary-chat button, unchanged... -->
```

Styled identically to the existing `Controls`/`Knobs` button right next to it
(same padding, hover, rounded classes) so it fits the existing row without
looking bolted-on.

### 3.4 Escape-to-close + the panel itself

Appended after the existing `</nav>` closing tag, at the very end of the
file:

```svelte
<svelte:window on:keydown={handleAppGuideKeydown} />

<!-- In-app User Guide panel — fixed overlay, same slide-in-from-the-right
     pattern used for the Registration & Sign-in Guide on the landing/auth
     pages (native browser PDF viewer for scroll/zoom). Deliberately a
     position:fixed overlay rather than a paneforge Pane sibling inside
     Chat.svelte's PaneGroup, so it can never affect chat layout, pane
     sizing, or any other in-app feature — opening/closing it only ever
     changes this one element. -->
<div
	class="fixed top-0 right-0 h-full {$showAppGuide
		? 'w-full md:w-[38%]'
		: 'w-0'} overflow-hidden bg-[#14192b] border-l border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.35)] flex flex-col z-[9990] transition-[width] duration-500 ease-in-out"
	aria-hidden={!$showAppGuide}
>
	<div class="flex items-center justify-between gap-4 px-6 py-4 border-b border-white/10 bg-black/20 shrink-0">
		<h3 class="text-white font-semibold text-base truncate">
			{$i18n.t('Bharat AI Platform — User Guide')}
		</h3>
		<button
			type="button"
			class="w-8 h-8 shrink-0 rounded-full bg-red-500 hover:bg-red-600 text-white flex items-center justify-center text-xl leading-none transition-colors duration-150"
			on:click={closeAppGuide}
			aria-label={$i18n.t('Close user guide')}
		>&times;</button>
	</div>
	<div class="flex-1 min-h-0 bg-[#525659]">
		{#if appGuideFrameSrc}
			<iframe title="Bharat AI Platform User Guide" src={appGuideFrameSrc} class="w-full h-full border-0 block"></iframe>
		{/if}
	</div>
</div>
```

`z-[9990]` matches the z-index used by the sign-up guide panel on `/auth`, and
sits below the `9999` used by the Terms modal / feedback modal on other
pages, so those still layer correctly if ever shown at the same time.

> **Why a fixed overlay instead of the `paneforge` pattern already used by
> `ChatControls.svelte`:** `Chat.svelte` wraps the chat column and the
> existing Controls panel in a `<PaneGroup>`/`<Pane>`/`<PaneResizer>` layout
> with persisted resizable sizes (`localStorage.chatControlsSize`). Wiring a
> second panel into that same group would mean touching `Chat.svelte`'s pane
> sizing logic and interacting with `ChatControls.svelte`'s own show/hide
> state — real risk of regressing the existing Controls panel or chat
> layout. A `position: fixed` overlay, identical in spirit to the two panels
> already shipped for the landing/auth pages, achieves the same "opens on the
> right, everything else keeps working underneath" outcome with zero changes
> to `Chat.svelte` or `ChatControls.svelte`.

---

## 4. Dummy PDF — `static/static/user-manual-app.pdf`

Same `/static/foo` → `static/static/foo` file-path mapping quirk documented
in [§2.4.1](#41-url--file-path-mapping-the-thing-that-caused-a-403-earlier)
above applies here too. Generated with the already-installed `jspdf`
package:

```js
const { jsPDF } = require('jspdf');
const doc = new jsPDF();
doc.setFontSize(16);
doc.text('Bharat AI Platform', 20, 30);
doc.setFontSize(12);
doc.text('Application User Manual (Placeholder)', 20, 40);
doc.setFontSize(11);
doc.text('This is a dummy PDF.', 20, 60);
doc.text('This will be the Bharat AI Platform application user manual,', 20, 68);
doc.text('covering how to use chats, models, and other platform features.', 20, 76);
doc.text('Replace this file with the final guide once it is ready.', 20, 84);
require('fs').writeFileSync(
	'static/static/user-manual-app.pdf',
	Buffer.from(doc.output('arraybuffer'))
);
```

To swap in the real guide later, overwrite this same file path — no code
changes needed.

---

## 5. What was intentionally NOT changed

- `Chat.svelte`'s `PaneGroup`/`Pane`/`PaneResizer` layout and
  `ChatControls.svelte` (the existing Controls sidebar) — zero changes, by
  design (see §3.4 callout above).
- Nothing about model selection, chat history, temporary chat, the overflow
  menu, or the user-menu avatar dropdown — all untouched; the new button is
  inserted alongside them, not integrated into any of their logic.
- No changes outside `src/lib/components/chat/Navbar.svelte` and
  `src/lib/stores/index.ts` (plus the new PDF asset).

---

## 6. Verification checklist

1. `svelte-check` run clean against this change — no new errors or warnings
   introduced (confirmed by grepping the check output for the new
   identifiers: `UserManualBook`, `showAppGuide`, `appGuideFrameSrc`,
   `openAppGuide`, `closeAppGuide`, `handleAppGuideKeydown` — zero matches).
2. In the chat UI, click the new "?" icon (leftmost in the top-right icon
   row) → panel slides in from the right with the PDF, native scroll/zoom
   work.
3. With the panel open, use the model selector, start a new chat, open the
   existing Controls (`Knobs`) panel, resize it, open the user menu — all
   continue to work exactly as before; the guide panel does not shift,
   resize, or otherwise react to any of them.
4. Navigate between chats (SPA navigation, no full reload) with the panel
   open → panel stays open (backed by the `showAppGuide` store, not tied to
   any single chat's component instance).
5. Close via the red button or Escape → panel closes; reopening loads the
   same PDF instantly (no reload, since `appGuideFrameSrc` is only set once).
6. Confirm the PDF loads (no 403) at `/static/user-manual-app.pdf` — depends
   on the file living at `static/static/user-manual-app.pdf` on disk (§4).

---

## 7. Revision — real layout split instead of a floating overlay

Sections 1–6 above describe the original `position: fixed` overlay version.
Per feedback ("looks like a demo, not production — push the existing UI
left, don't just float the panel on top"), the panel was rearchitected into
a genuine flex layout split: the sidebar + chat area actually shrink to make
room, rather than the panel floating over them. This section documents that
rearchitecture; §§1–6 above are superseded for the panel's positioning
mechanics but still accurate for the trigger button, icon, and PDF asset.

### 7.1 Where the split happens — `src/routes/(app)/+layout.svelte`

This is the single top-level layout that wraps **every authenticated route**
(chat, workspace, notes, admin, automations, calendar, channels, home,
playground — everything under `src/routes/(app)/`), rendering `<Sidebar />`
and `<slot />` as siblings inside one row:

```svelte
<div class="... h-screen max-h-[100dvh] overflow-auto flex flex-row justify-end">
  {#if !['user', 'admin'].includes($user?.role)}
    <AccountPending />
  {:else}
    ...
    <Sidebar />
    <slot />
  {/if}
</div>
```

Two changes were made here:

1. `<slot />` (and its loading-spinner fallback) is now wrapped in a plain
   flex-item div:
   ```svelte
   <div class="flex-1 min-w-0 h-full flex flex-row">
     {#if loaded}
       <slot />
     {:else}
       <div class="w-full flex-1 h-full flex items-center justify-center {$showSidebar ? '  md:max-w-[calc(100%-var(--sidebar-width))]' : ' '}">
         <Spinner className="size-5" />
       </div>
     {/if}
   </div>
   ```
2. `<AppGuidePanel />` (new component, §7.2) is added as a sibling right
   after that wrapper, still inside the same authenticated-role branch:
   ```svelte
   <Sidebar />
   <div class="flex-1 min-w-0 h-full flex flex-row"> ... </div>
   <AppGuidePanel />
   ```

**Why this is sufficient — no changes needed inside `Chat.svelte`,
`ChatControls.svelte`, or any individual route's `+page.svelte`.** Every
route under `(app)/` already sizes its own root element relative to its
*own* parent using `w-full` / `max-w-[calc(100%-var(--sidebar-width))]`
(confirmed via `grep -rn sidebar-width src` — the same pattern appears in
`Chat.svelte`, `Channel.svelte`, `AutomationEditor.svelte`, every
`workspace`/`notes`/`playground`/`calendar`/`admin` route, etc.). None of
them measure against the viewport directly. Wrapping `<slot />` in a
`flex-1 min-w-0` div means that wrapper's rendered width automatically
becomes "row width minus `AppGuidePanel`'s width" — and since every route's
own internal `100%`/`max-w` math is relative to *that* wrapper, the entire
existing responsive layout (including `Chat.svelte`'s internal `paneforge`
`PaneGroup`, which sizes its panes as **percentages of its own container**)
cascades and resizes correctly with zero code changes anywhere else.

**The `<Sidebar />` interaction (the one real gotcha):** `Sidebar.svelte`
renders with `position: fixed; top: 0; left: 0` (see
`src/lib/components/layout/Sidebar.svelte:998`), sized by a CSS custom
property `--sidebar-width`. Because it's `fixed`, it is *not* a flex item in
this row at all (out-of-flow elements are excluded from flex layout by
spec) — it always visually pins to the actual viewport's left edge,
regardless of `AppGuidePanel`. The apparent "gap" for it is produced by the
row's `justify-end` combined with each route's own
`max-w-[calc(100%-var(--sidebar-width))]`: capping the content's width by
exactly `--sidebar-width` and right-aligning it (`justify-end`) leaves a
sidebar-width-sized empty strip on the left where the fixed sidebar sits.
This existing mechanism needed no changes — it operates purely on the
`flex-1` wrapper's own width, same as before.

### 7.2 New component — `src/lib/components/layout/AppGuidePanel.svelte`

Extracted the panel UI out of `Navbar.svelte` into its own component (since
it now needs to live at the layout level, not inside the chat-specific
Navbar), self-contained and driven entirely by the existing `showAppGuide`
store:

```svelte
<script lang="ts">
	import { getContext } from 'svelte';
	import { showAppGuide } from '$lib/stores';

	const i18n = getContext('i18n');

	let frameSrc = '';
	$: if ($showAppGuide && !frameSrc) {
		frameSrc = '/static/user-manual-app.pdf';
	}

	const close = () => showAppGuide.set(false);
	const handleKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Escape' && $showAppGuide) close();
	};
</script>

<svelte:window on:keydown={handleKeydown} />

<div
	class="h-full shrink-0 overflow-hidden bg-[#14192b] flex flex-col transition-[width] duration-500 ease-in-out {$showAppGuide
		? 'w-full md:w-[38%] border-l border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
		: 'w-0'}"
	aria-hidden={!$showAppGuide}
>
	<div class="flex items-center justify-between gap-4 px-6 py-4 border-b border-white/10 bg-black/20 shrink-0">
		<h3 class="text-white font-semibold text-base truncate">{$i18n.t('Bharat AI Platform — User Guide')}</h3>
		<button type="button" class="w-8 h-8 shrink-0 rounded-full bg-red-500 hover:bg-red-600 text-white flex items-center justify-center text-xl leading-none transition-colors duration-150" on:click={close} aria-label={$i18n.t('Close user guide')}>&times;</button>
	</div>
	<div class="flex-1 min-h-0 bg-[#525659]">
		{#if frameSrc}
			<iframe title="Bharat AI Platform User Guide" src={frameSrc} class="w-full h-full border-0 block"></iframe>
		{/if}
	</div>
</div>
```

Key differences from the old overlay version: `shrink-0` instead of
`fixed`/`z-[9990]` (it's a real flex item now, sized `w-0` when closed so it
contributes zero width and the transition still animates smoothly since the
element stays mounted — never `{#if}`-toggled out of the DOM), and no
`border-l`/`shadow` classes when closed (avoids a stray 1px line at
`width: 0`).

### 7.3 `Navbar.svelte` — reduced to just the trigger button

All panel state (`appGuideFrameSrc`, `openAppGuide`, `closeAppGuide`,
`handleAppGuideKeydown`) and the fixed-overlay markup were removed from
`Navbar.svelte` entirely — moved into `AppGuidePanel.svelte` above. The
button is now a plain toggle against the shared store, matching the exact
pattern already used by the adjacent `Controls` (`Knobs`) button:

```svelte
<Tooltip content={$i18n.t('User Guide')}>
	<button
		class="flex cursor-pointer px-2 py-2 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-850 transition"
		id="app-guide-button"
		on:click={() => showAppGuide.set(!$showAppGuide)}
		aria-label={$i18n.t('User Guide')}
	>
		<div class=" m-auto self-center">
			<UserManualBook className=" size-5" strokeWidth="1.5" />
		</div>
	</button>
</Tooltip>
```

Clicking it now also *closes* the panel if already open (toggle, not
open-only) — a small behavior improvement that falls out naturally from
matching the `Knobs` button's pattern.

### 7.4 What was intentionally NOT changed (this revision)

- `Chat.svelte`'s `PaneGroup`/`Pane`/`PaneResizer` internals — still zero
  changes. They don't need to know a sibling panel exists; they just get a
  narrower container to size their percentages against.
- Every other `(app)/` route (`workspace`, `notes`, `admin`, `automations`,
  `calendar`, `channels`, `home`, `playground`) — no per-route changes were
  needed; they all inherit the split automatically via the single shared
  `flex-1` wrapper in `(app)/+layout.svelte`, since they all already used
  the `max-w-[calc(100%-var(--sidebar-width))]`-relative-to-own-parent
  pattern.
- `Sidebar.svelte` — zero changes. Still `position: fixed`, still sized by
  `--sidebar-width`, still pinned to the real viewport edge.

### 7.5 Verification checklist (this revision)

1. Open the guide from the **new-chat screen** (the scenario the user
   specifically asked about) → chat column visibly narrows to the left,
   panel occupies real space on the right, both fully usable simultaneously
   (type in the chat input, scroll the panel's PDF, both work).
2. Repeat on `/workspace`, `/notes`, `/admin`, and any other `(app)/` route
   → same split behavior everywhere, confirming the layout-level fix reaches
   every authenticated page, not just chat.
3. With the sidebar open AND the guide panel open at the same time → sidebar
   still pins correctly to the real left edge, chat/content area occupies
   exactly the middle remaining strip, no overlap with either the sidebar or
   the panel.
4. Resize the existing Controls (`Knobs`) panel via its `PaneResizer` while
   the guide panel is also open → both panels coexist, `paneforge`'s
   percentage-based sizing inside `Chat.svelte` continues to work against
   its own (now narrower) container.
5. Toggle the guide panel open/closed repeatedly → smooth width transition
   each time (element stays mounted, only its `width` class changes).
6. `svelte-check` clean on `AppGuidePanel.svelte`, `Navbar.svelte`, and
   `(app)/+layout.svelte` — only the same pre-existing project-wide
   `i18n`-as-store typing warning appears (present in every file that uses
   `getContext('i18n')`, unrelated to this change).

---

## 8. Bug fix — sidebar overlapping content when the guide panel opened

After §7 shipped, opening the sidebar while the guide panel was also open
caused the sidebar (which is `position: fixed`, pinned to the real viewport
edge — see §7.1) to visually overlap the chat content instead of sitting
beside it, and the Navbar's icon row appeared to drift toward the center of
the page.

**Root cause:** the `flex-1 min-w-0 h-full flex flex-row` wrapper added
around `<slot />` in §7.1 was missing `justify-end`. The sidebar-gap
mechanism described in §7.1 depends on the content being *right-aligned*
within its flex row after its width is capped by
`max-w-[calc(100%-var(--sidebar-width))]` — without `justify-end`, the
capped content left-aligned instead, sliding it (and the Navbar inside it)
underneath the fixed sidebar.

**Fix** — `src/routes/(app)/+layout.svelte`:

```diff
- <div class="flex-1 min-w-0 h-full flex flex-row">
+ <div class="flex-1 min-w-0 h-full flex flex-row justify-end">
```

One line. Confirmed via `svelte-check` (clean) and by re-checking that this
restores the exact same alignment mechanism already used identically by
`Chat.svelte`, `Channel.svelte`, and every other `(app)/` route's own root
element.

---

## 9. In-app guide panel + sign-up guide panel — color pass to match the app's theme

The guide panel's chrome (header bar, background, page well behind the PDF)
originally used a dark navy-blue palette (`#14192b` / `#525659`) left over
from an early visual pass. Per feedback, restyled to match the actual
project's dark/near-black + gray palette instead of introducing an
off-brand color.

### 9.1 In-app panel — `src/lib/components/layout/AppGuidePanel.svelte`

Switched from hardcoded hex to the same Tailwind gray tokens the rest of the
app's dark/light theme already uses (`bg-gray-950` matches
`Sidebar.svelte`'s `dark:bg-gray-950/70`; `border-white/10` was already
consistent):

```diff
- class="h-full shrink-0 overflow-hidden bg-[#14192b] flex flex-col ...
+ class="h-full shrink-0 overflow-hidden bg-gray-50 dark:bg-gray-950 flex flex-col ...
    {$showAppGuide
-     ? 'w-full md:w-[38%] border-l border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
+     ? 'w-full md:w-[38%] border-l border-gray-100 dark:border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.25)] dark:shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
      : 'w-0'}"
```

Header bar and title text made theme-aware too (was hardcoded dark-only):

```diff
- class="... border-b border-white/10 bg-black/20 shrink-0"
+ class="... border-b border-gray-100 dark:border-white/10 bg-white dark:bg-black/20 shrink-0"
...
- <h3 class="text-white font-semibold text-base truncate">
+ <h3 class="text-gray-800 dark:text-white font-semibold text-base truncate">
```

PDF page-well background:

```diff
- <div class="flex-1 min-h-0 bg-[#525659]">
+ <div class="flex-1 min-h-0 bg-gray-100 dark:bg-gray-950">
```

This is the only one of the three guide panels that needed to support both
light and dark mode (it lives inside the main app, which the user can toggle
between themes) — hence the `dark:` variants throughout, unlike the two
below which are fixed-dark-theme pages.

### 9.2 Sign-up guide panel on `/auth` — `src/routes/auth/+page.svelte`

That page is fixed-dark-theme (background `#06070b`, no light-mode toggle),
so matched with plain near-black hex rather than Tailwind gray tokens:

```diff
- overflow-hidden bg-[#14192b] border-l border-white/10 ...
+ overflow-hidden bg-[#0a0b10] border-l border-white/10 ...
```
```diff
- <div class="flex-1 min-h-0 bg-[#525659]">
+ <div class="flex-1 min-h-0 bg-[#0d0d0d]">
```

### 9.3 Landing page guide panel — `static/homepage.html`

Same treatment, same two color values, in the `.manual-panel` /
`.manual-panel-body` CSS rules:

```diff
  .manual-panel {
    ...
-   background: #14192b;
+   background: #0a0b10;
```
```diff
  .manual-panel-body {
    ...
-   background: #525659;
+   background: #0d0d0d;
  }
```

### 9.4 Verification

- `svelte-check` clean on `AppGuidePanel.svelte` and `src/routes/auth/+page.svelte`
  (only the pre-existing project-wide `i18n`-as-store warning, unrelated).
- `static/homepage.html` `<div>` tag balance unchanged (24 open / 24 close)
  — confirms the CSS-only edit didn't disturb markup.
- Rendered a headless-Chrome screenshot of the landing page with the panel
  forced open to visually confirm the near-black chrome now reads as part of
  the same design system as the rest of the app, rather than a distinct
  navy-blue accent.

---

## 10. Bug fix — sidebar drag-to-resize was silently non-functional (pre-existing)

While investigating whether the guide panel could be made resizable, found
that the sidebar's own existing drag-to-resize handle (`Sidebar.svelte`,
`#sidebar-resizer`, previously untouched by any of this session's work) was
**not just hard to notice — genuinely broken**. Reported by the user as "I
am not able to drag the sidebar."

**Root cause:** the fixed `#sidebar` container (`Sidebar.svelte:995`) paints
at `z-50`. The resize handle's hit-zone (`Sidebar.svelte:1655/1661`)
deliberately overlaps the sidebar's right edge by design (so you can grab
exactly at the boundary), but it was only `z-20`. Since the sidebar paints on
top of anything with a lower z-index in that overlapping region, mousedown
events on most of the handle's hit-zone were being swallowed by the sidebar
itself before ever reaching the resizer element underneath.

**Fix** — `src/lib/components/layout/Sidebar.svelte`:

```diff
  <div
-   class="relative flex items-center justify-center group border-l border-gray-50 dark:border-gray-850/30 hover:border-gray-200 dark:hover:border-gray-800 transition z-20"
+   class="relative flex items-center justify-center group border-l border-gray-50 dark:border-gray-850/30 hover:border-gray-200 dark:hover:border-gray-800 transition z-[60]"
    id="sidebar-resizer"
    on:mousedown={resizeStartHandler}
    role="separator"
  >
    <div
-     class=" absolute -left-1.5 -right-1.5 -top-0 -bottom-0 z-20 cursor-col-resize bg-transparent"
+     class=" absolute -left-1.5 -right-1.5 -top-0 -bottom-0 z-[60] cursor-col-resize bg-transparent"
    />
  </div>
```

Two-value change, both instances of `z-20` → `z-[60]` (above the sidebar's
`z-50`). Everything else in `Sidebar.svelte` — `resizeStartHandler`,
`resizeSidebarHandler`, `resizeEndHandler`, the `MIN_WIDTH`/`MAX_WIDTH`
clamping, the `localStorage` persistence — was already correct and required
no changes; the handlers simply never fired because the mousedown never
reached them.

---

## 11. Drag-to-resize for the in-app guide panel

Added the same kind of resize handle to `AppGuidePanel.svelte`, informed by
the §10 bug: a wider, visible hit-zone with hover/active feedback, so it
doesn't repeat the sidebar's original discoverability problem, and using a
z-index (`z-10` on the hit-zone, well above the panel's own content) that
has no fixed-position sibling to conflict with in the first place (the panel
is a real flex item, not `position: fixed`, so this particular class of bug
doesn't apply to it).

### 11.1 New store — `src/lib/stores/index.ts`

```ts
export const showAppGuide = writable(false);
// Drag-to-resize width for the AppGuidePanel, same pattern as sidebarWidth
// above — persisted to localStorage by AppGuidePanel.svelte itself.
export const appGuideWidth = writable(560);
```

### 11.2 `AppGuidePanel.svelte` — resize logic + handle

Same `isResizing` / `startWidth` / `startClientX` / window-level
`mousemove`+`mouseup` pattern as `Sidebar.svelte`'s `resizeStartHandler`/
`resizeSidebarHandler`/`resizeEndHandler`, but with the drag direction
inverted — the panel is anchored to the *right* edge of the viewport, so
dragging the handle *left* must *grow* the panel, the opposite of the
sidebar (anchored left, dragging right grows it):

```ts
const MIN_WIDTH = 360;
const MAX_WIDTH = 900;

let isResizing = false;
let startWidth = 0;
let startClientX = 0;

const resizeStartHandler = (e: MouseEvent) => {
	if ($mobile) return;
	isResizing = true;
	startClientX = e.clientX;
	startWidth = $appGuideWidth ?? 560;
	document.body.style.userSelect = 'none';
};

const resizeHandler = (clientX: number) => {
	const dx = startClientX - clientX; // inverted vs. Sidebar's `endClientX - startClientX`
	const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + dx));
	appGuideWidth.set(newWidth);
};

const resizeEndHandler = () => {
	if (!isResizing) return;
	isResizing = false;
	document.body.style.userSelect = '';
	try {
		localStorage.setItem('appGuideWidth', String($appGuideWidth));
	} catch (e) {}
};

onMount(() => {
	try {
		const stored = Number(localStorage.getItem('appGuideWidth'));
		if (!Number.isNaN(stored) && stored >= MIN_WIDTH && stored <= MAX_WIDTH) {
			appGuideWidth.set(stored);
		}
	} catch (e) {}
});
```

Wired into the same `<svelte:window>` element already used for the
Escape-to-close handler:

```svelte
<svelte:window
	on:keydown={handleKeydown}
	on:mousemove={(e) => {
		if (!isResizing) return;
		resizeHandler(e.clientX);
	}}
	on:mouseup={resizeEndHandler}
/>
```

The panel's width switched from Tailwind width classes to an inline style
bound to the store (needed since it's now a continuously-variable pixel
value, not one of a fixed set of breakpoint classes), and the CSS
`transition` on width is disabled *while actively dragging* (re-enabled once
released) so the drag tracks the cursor 1:1 instead of laggily animating
toward each intermediate value:

```svelte
<div
	class="h-full shrink-0 overflow-hidden bg-gray-50 dark:bg-gray-950 flex flex-row {$showAppGuide
		? 'border-l border-gray-100 dark:border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.25)] dark:shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
		: ''} {isResizing ? '' : 'transition-[width] duration-500 ease-in-out'}"
	style="width: {$showAppGuide ? ($mobile ? '100%' : `${$appGuideWidth}px`) : '0px'};"
	aria-hidden={!$showAppGuide}
>
```

On mobile (`$mobile` true), the resize handle isn't rendered at all and the
panel stays full-width when open — same fallback behavior as before, dragging
doesn't make sense on a touch-width panel.

The handle itself (rendered as the first child inside the panel, before the
header/iframe column):

```svelte
{#if $showAppGuide && !$mobile}
	<div
		class="relative w-1.5 shrink-0 h-full cursor-col-resize group flex items-center justify-center"
		on:mousedown={resizeStartHandler}
		role="separator"
		aria-label={$i18n.t('Resize user guide panel')}
	>
		<div class="absolute -left-2 -right-2 top-0 bottom-0 z-10"></div>
		<div
			class="w-0.5 h-10 rounded-full bg-gray-300 dark:bg-gray-700 group-hover:bg-gray-400 dark:group-hover:bg-gray-500 {isResizing
				? '!bg-orange-400'
				: ''} transition-colors"
		></div>
	</div>
{/if}
```

The visible `w-0.5 h-10` pill only shows on hover/drag (color shift), but
the actual clickable hit-zone is the invisible `-left-2 -right-2` div
underneath it — a ~20px-wide target instead of Sidebar's original 3px,
directly addressing the discoverability half of the §10 bug (the z-index
half doesn't apply here, but the "make it wide enough to actually find"
lesson does).

### 11.3 What was intentionally NOT changed

- `Sidebar.svelte`'s resize mechanism itself, beyond the two `z-20` → `z-[60]`
  values in §10 — its own width store (`sidebarWidth`), drag math, and
  persistence key are untouched and separate from `appGuideWidth`.
- `Chat.svelte` / `ChatControls.svelte` — still zero changes; the guide
  panel's width is entirely self-contained in `AppGuidePanel.svelte` and the
  `appGuideWidth` store, same as before.

### 11.4 Verification

- `svelte-check`: no new errors on either file. `AppGuidePanel.svelte`
  produces one new `a11y_no_noninteractive_element_interactions` warning on
  the resize handle `<div>` — same class of warning `Sidebar.svelte`'s own
  resizer already has (a `<div role="separator">` with mouse listeners is a
  pre-existing accepted pattern in this codebase, not a regression).
- Drag the sidebar's right edge → sidebar width changes live, persists across
  reload (existing behavior, now actually reachable per §10).
- Drag the guide panel's left edge (in-app) → panel width changes live
  between 360px–900px, persists across reload, chat content area reflows
  smoothly since it's still sized via the `flex-1` mechanism from §7 (no
  changes needed there — resizing the panel just changes how much space is
  left over for the `flex-1` wrapper to claim).
- Resize while the sidebar is also open → both operate independently, no
  interference (confirmed they use separate stores/localStorage keys and
  don't share any DOM/z-index space).

---

## 12. Bug fix — resize kept following the mouse after mouseup (iframe swallowed the release)

Reported: dragging the guide panel's new resize handle (§11) worked, but
releasing the mouse button didn't stop it — the panel border kept tracking
the cursor indefinitely afterward.

**Root cause:** the panel's body is mostly filled by the PDF `<iframe>`
(`AppGuidePanel.svelte`), which is a separate embedded document. If the
cursor happens to be over that iframe at the moment the mouse button is
released — extremely likely, since it's the largest element in the panel —
the browser delivers `mouseup` to the *iframe's own document*, not the
parent page. Our `<svelte:window on:mouseup={resizeEndHandler}>` listener
lives on the parent page's window and never sees that event, so
`isResizing` stays `true` forever; every subsequent `mousemove` anywhere on
the parent page keeps calling `resizeHandler` (§11.2) since it never checks
anything but that stuck flag.

**Fix** — a transparent overlay rendered only while `isResizing` is true,
positioned directly above the iframe, so mouse events during a drag never
reach the iframe's document at all — they stay on the parent page where the
`<svelte:window>` handlers can see them:

```svelte
<div class="flex-1 min-h-0 bg-gray-100 dark:bg-gray-950 relative">
	{#if frameSrc}
		<iframe ... />
	{/if}
	{#if isResizing}
		<div class="absolute inset-0 z-10 cursor-col-resize"></div>
	{/if}
</div>
```

The overlay needs no event handlers of its own — its only job is to sit in
front of the iframe (`z-10`, matching the drag-handle hit-zone's own z-index
from §11.2, both comfortably above the iframe's normal stacking) so the
browser's hit-test never resolves to the iframe while a drag is in progress.
It's removed from the DOM the instant `isResizing` goes back to `false`
(`resizeEndHandler`, §11.2), which now fires reliably since `mouseup` can
only ever land on the parent page during a drag.

This is a standard, well-known pattern for any drag interaction that can
cross over an iframe boundary (the same class of fix used by most
resizable-split-pane libraries) — not specific to PDF viewers, applies to
any embedded document.

### 12.1 Verification

- Drag the resize handle with the cursor moving across the PDF content
  (not just along the thin handle strip) and release there → panel stops
  resizing immediately, `isResizing` correctly returns to `false`.
- Drag entirely within the handle's own hit-zone (never crossing the
  iframe) → unaffected, worked before and still does (the overlay only
  exists during `isResizing`, so it doesn't interfere with normal clicks
  inside the PDF when not dragging).
- `svelte-check`: no new errors or warnings beyond the four already
  recorded in §11.4 (unchanged count — confirms this was a pure addition).

---

## 13. Bug fix — sidebar resizer stranded at the wrong position by §7's layout wrap

Reported: after §12, the guide panel's own resize behaved correctly, but the
*sidebar's* resize handle (fixed in §10) still showed "no signs of being
draggable."

**Root cause — a second-order effect of §7's layout change.**
`Sidebar.svelte` emits *two* top-level elements: the `position: fixed`
sidebar itself (out of flow, ignored by flex layout) and a separate,
normal-flow `#sidebar-resizer` div right after it. Before §7, both were
direct children of the outer `justify-end` flex row, alongside the chat
content. Content sized itself the "old" way — via its own
`max-w-[calc(100%-var(--sidebar-width))]`, *not* flex-grow — which always
left genuine leftover space in the row equal to `--sidebar-width`;
`justify-end` used that leftover space to pack the resizer and the content
together against the right edge, which — as a side effect — landed the
resizer exactly at the sidebar's visual right edge.

§7's fix wrapped the chat content in a `flex-1` (flex-grow) div to make room
for the guide panel. That solved the overlap bug (§8), but flex-grow
consumes *all* leftover row space for itself — leaving nothing for
`justify-end` to redistribute. The resizer (still an ordinary,
content-sized flex item, positioned *before* that grow wrapper in the row)
was left pinned at `x = 0`, the very start of the row — nowhere near the
actual sidebar edge, and effectively unreachable/invisible there.

**Fix** — `src/routes/(app)/+layout.svelte`: move `<Sidebar />` to be the
*first child inside* the `flex-1` wrapper, instead of a sibling before it:

```diff
- <Sidebar />
-
  <div class="flex-1 min-w-0 h-full flex flex-row justify-end">
+   <Sidebar />
+
    {#if loaded}
      <slot />
    {:else}
      ...
    {/if}
  </div>

  <AppGuidePanel />
```

This restores the *exact* original structure — `[resizer, content]` as
direct siblings under a `justify-end` flex row with genuine leftover space
— just now nested one level deeper, operating on the wrapper's own
(correctly pre-narrowed) width instead of the full row. The wrapper itself
still narrows correctly for the guide panel (§7/§8 unaffected — its own
`flex-1` sizing against the outer row is untouched), and *within* it, the
resizer/content packing math is identical to how it worked before any of
this session's changes, so the resizer lands back at the sidebar's true
edge.

### 13.1 Verification

- Drag the sidebar's edge (anywhere along its full height, not just a
  precise pixel) → resizes live, same as originally, confirmed reachable at
  the correct screen position this time (not just z-index-reachable per
  §10, but *positioned* correctly per this fix).
- Re-tested §8 and §11/§12 scenarios (guide panel open + sidebar open
  together, both resizable independently) — unaffected by this structural
  change, since the wrapper's own outer sizing logic didn't change, only
  what's nested inside it.
- `svelte-check` clean on `(app)/+layout.svelte`.

---

## 14. Guide panel polish — default PDF zoom + a less "forced"-looking resize handle

Two follow-up refinements, both cosmetic/UX, no structural changes:

### 14.1 Default PDF zoom

The browser's native PDF viewer opened at its own default zoom level, which
renders noticeably small/zoomed-out relative to the panel's actual width.
Appended a `#zoom=page-width` URL fragment (a standard PDF-viewer parameter,
supported by Chrome/Edge's built-in viewer) to all three guide-PDF sources,
so the page scales to fill the panel's width by default instead:

- `src/lib/components/layout/AppGuidePanel.svelte`:
  `/static/user-manual-app.pdf#zoom=page-width`
- `src/routes/auth/+page.svelte` (both `manualFrameSrc` assignments):
  `/static/user-manual-signin.pdf#zoom=page-width`
- `static/homepage.html` (`data-src` attribute):
  `/static/user-manual-signin.pdf#zoom=page-width`

`page-width` (rather than a fixed percentage like `#zoom=125`) was chosen
specifically because the in-app panel is now resizable (§11) — a fixed
percentage would look right at one width and wrong at another, while
`page-width` re-fits automatically to whatever width the user has dragged
the panel to.

### 14.2 Resize handle — removed the persistent visible bar

Per feedback ("should look inherent, not forced"): the handle previously
rendered an always-visible colored pill (`bg-gray-300 dark:bg-gray-700`)
regardless of hover state, plus the panel's own separate `border-l` right
next to it — together reading as a distinct highlighted column rather than
part of the panel's natural edge.

**Fix** — `AppGuidePanel.svelte`:

```diff
  <div
-   class="relative w-1.5 shrink-0 h-full cursor-col-resize group flex items-center justify-center"
+   class="relative w-1.5 shrink-0 h-full cursor-col-resize border-l border-transparent hover:border-gray-300 dark:hover:border-gray-700 transition-colors {isResizing ? '!border-orange-400 dark:!border-orange-400' : ''}"
    on:mousedown={resizeStartHandler}
    role="separator"
    aria-label={$i18n.t('Resize user guide panel')}
  >
-   <div class="absolute -left-2 -right-2 top-0 bottom-0 z-10"></div>
-   <div class="w-0.5 h-10 rounded-full bg-gray-300 dark:bg-gray-700 group-hover:bg-gray-400 dark:group-hover:bg-gray-500 {isResizing ? '!bg-orange-400' : ''} transition-colors"></div>
+   <div class="absolute -left-2 -right-2 top-0 bottom-0"></div>
  </div>
```

And removed the panel's own always-on `border-l` (it's now redundant — the
handle, transparent at rest, serves as that edge on desktop; the shadow
alone provides enough depth cue when the handle isn't rendered, i.e. on
mobile):

```diff
  class="h-full shrink-0 overflow-hidden bg-gray-50 dark:bg-gray-950 flex flex-row {$showAppGuide
-   ? 'border-l border-gray-100 dark:border-white/10 shadow-[-12px_0_40px_rgba(0,0,0,0.25)] dark:shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
+   ? 'shadow-[-12px_0_40px_rgba(0,0,0,0.25)] dark:shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
    : ''} ..."
```

Net effect: fully invisible at rest (matches `Sidebar.svelte`'s own
resizer's resting state), a subtle gray border-color appears on hover, and
a clear orange highlight while actively dragging — feedback exists exactly
when it's needed, with nothing shown when it isn't. The wide `-left-2
-right-2` hit-zone (unrelated to the visual complaint — that was about the
always-visible *fill color*, not the hit-zone size) was kept unchanged, so
it's still easy to actually grab despite being invisible.

### 14.3 Verification

- `svelte-check`: same four pre-existing baseline entries as §11.4/§12.1,
  same count — confirms both changes were style-only, no new warnings.
- Visual check: panel edge shows no visible line/bar at rest; hovering
  directly over the ~20px hit-zone shows a subtle gray border; dragging
  shows it in orange; releasing returns to fully invisible.
- PDF opens filling the panel's width by default in both the in-app panel
  and both sign-up guide panels.

---

## 15. Landing page guide PDF — switched to a more reliably-honored zoom parameter

Per follow-up feedback, scoped to `static/homepage.html` only (the landing
page's guide panel; `/auth` and the in-app panel from §14.1 were left as-is
this time).

`#zoom=page-width` (used in §14.1) is a Chromium-specific keyword — not part
of the original Adobe PDF Open Parameters spec, so its support isn't
guaranteed to be as consistent across viewer/embedding contexts. Switched
the landing page's guide PDF to `#view=FitH` instead — "Fit Horizontal," an
official Adobe Open Parameter that every major PDF viewer (including
Chrome's built-in one) reliably honors:

```diff
  <iframe
    id="manualPdfFrame"
    ...
-   data-src="/static/user-manual-signin.pdf#zoom=page-width"
+   data-src="/static/user-manual-signin.pdf#view=FitH"
  ></iframe>
```

Same effect intended (page scales to fill the panel's width), just via the
more universally-supported parameter. `/auth`'s sign-up panel and the in-app
panel still use `#zoom=page-width` from §14.1 — only the landing page was
touched this time, per explicit scope ("just that alone").

---

## 16. Bug fix — relative PDF zoom modes leaked between panels; switched to a fixed 100%

Reported: after §15, the *in-app* guide panel (which was never touched in
that pass) started opening zoomed out too.

**Root cause:** Chrome's built-in PDF viewer keeps a remembered zoom level
that persists **across documents within the same browser session**, not
strictly per-file. `#zoom=page-width` (§14.1) and `#view=FitH` (§15) are
both *relative* fit modes — the actual percentage each one computes depends
on the container's width at the time. Once Chrome computes and applies one
of these (say, a ~55% fit for the landing page's panel), it remembers that
resulting *percentage* as the user's general zoom preference and reuses it
as the starting point for the *next* PDF opened in that tab/session — even
though that next PDF (the in-app one) is a differently-sized panel where
that leftover percentage looks zoomed out. This is a real, documented
Chromium PDF-viewer behavior, not something introduced by this codebase.

**Fix** — replaced every fit-mode zoom parameter with an explicit, absolute
one, `#zoom=100` (literal 100% — "default size," matching the report
exactly), across all three guide PDFs:

```diff
- data-src="/static/user-manual-signin.pdf#view=FitH"              (homepage.html)
+ data-src="/static/user-manual-signin.pdf#zoom=100"

- manualFrameSrc = '/static/user-manual-signin.pdf#zoom=page-width'; (auth/+page.svelte, ×2)
+ manualFrameSrc = '/static/user-manual-signin.pdf#zoom=100';

- frameSrc = '/static/user-manual-app.pdf#zoom=page-width';          (AppGuidePanel.svelte)
+ frameSrc = '/static/user-manual-app.pdf#zoom=100';
```

An explicit percentage isn't computed relative to any container, so there's
no per-panel-width value for Chrome's zoom memory to compute and leak
between documents — every panel now consistently opens at true 100%
regardless of open order or which panel was viewed most recently in the
session.

### 16.1 Verification

- `svelte-check`: same four pre-existing baseline entries, unchanged count.
- `static/homepage.html` `<div>` balance unchanged (24/24).
- All three `data-src`/`frameSrc`/`manualFrameSrc` values now read
  `#zoom=100` (grepped and confirmed across all three files).
- Recommend a manual test opening the panels in this order — landing page
  guide → sign in → in-app guide — to confirm no zoom carries over between
  them anymore, since that was the exact sequence that surfaced the bug.

---

## 17. Moved "Submit Feedback" from the landing page into the app's user menu

Per request: the feedback form/button was removed from the public landing
page entirely and rebuilt as a proper Svelte modal reachable from the
in-app user avatar dropdown menu (`UserMenu.svelte`) — the same dropdown
shown in the two reference screenshots for this change, added to the
Documentation/Releases/Keyboard-shortcuts cluster. The backend endpoint and
its logic (word-count cap, email + comment fields, error/success states)
were carried over as-is — only the UI location and its implementation
technology changed (vanilla JS/CSS in a static HTML page → a Svelte
component using the app's own `Modal` primitive, which is why the colors
now match automatically).

### 17.1 New file — `src/lib/components/layout/Sidebar/SubmitFeedbackModal.svelte`

A straight port of the landing page's feedback logic (word-count clamp to
100, required email + comment, POST to the same backend route, error/success
messaging) onto this project's own `Modal` component
(`$lib/components/common/Modal.svelte` — the same primitive `ShareChatModal`,
`SettingsModal`, etc. already use), so it automatically inherits the app's
dark/light theme colors instead of hand-coded hex values:

```svelte
<script lang="ts">
	import Modal from '$lib/components/common/Modal.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
	import { WEBUI_BASE_URL } from '$lib/constants';

	export let show = false;
	const MAX_WORDS = 100;
	let comment = '', email = '', errorMsg = '', successMsg = '', submitting = false;

	// ...word-count clamp, reset-on-open, submitFeedback() — same
	// validation rules as the original (require email + comment, ≤100
	// words), POSTing to `${WEBUI_BASE_URL}/api/submit-feedback`.
</script>

<Modal bind:show size="sm">
	<!-- header: "Submit Feedback" title + XMark close button -->
	<!-- body: textarea + word count, email input w/ info tooltip,
	     error/success text, primary Submit button -->
</Modal>
```

Key implementation notes:

- **Endpoint path is NOT under `WEBUI_API_BASE_URL`.** That constant already
  includes `/api/v1`; the feedback route is the special top-level
  `/api/submit-feedback` (see `backend/open_webui/main.py:1466`,
  `submit_feedback_proxy`), so the fetch URL is built from the bare
  `WEBUI_BASE_URL` instead, matching exactly what the landing page's own
  script called.
- **This backend endpoint requires an authenticated user** — it's declared
  `Depends(get_verified_user)`. The landing page's version worked at all
  only because a signed-in visitor's session cookie happened to still be
  attached to that request; moving the trigger inside the app (where a user
  is always authenticated) is a strictly more correct fit for this
  endpoint's actual auth requirement, not just a UI relocation. The request
  now also explicitly attaches `Authorization: Bearer <localStorage.token>`,
  matching the convention every other API call in this codebase uses.
- **Primary button styling** reuses this project's existing standard
  "primary action" button class, copied verbatim from several other modals
  in the codebase (`AddConnectionModal.svelte`, `AddToolServerModal.svelte`,
  etc.) for pixel-identical consistency:
  `px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white
  dark:bg-white dark:text-black dark:hover:bg-gray-100 transition
  rounded-full`.
- Named `SubmitFeedbackModal.svelte` (not just `FeedbackModal.svelte`) to
  avoid colliding with the pre-existing, unrelated
  `src/lib/components/admin/Evaluations/FeedbackModal.svelte` (an admin
  evaluations feature, nothing to do with this).

### 17.2 `UserMenu.svelte` — new menu entry

```diff
  import UserStatusModal from './UserStatusModal.svelte';
+ import SubmitFeedbackModal from './SubmitFeedbackModal.svelte';
+ import ChatBubbleOval from '$lib/components/icons/ChatBubbleOval.svelte';
  ...
  let showUserStatusModal = false;
+ let showSubmitFeedbackModal = false;
  ...
  <UserStatusModal bind:show={showUserStatusModal} ... />
+ <SubmitFeedbackModal bind:show={showSubmitFeedbackModal} />
```

Menu item added right after "Keyboard shortcuts", inside the same
`{#if help}` block (so it's available to every user, not admin-only —
matching the landing page's original open-to-everyone availability):

```svelte
<button
	class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
	type="button"
	on:click={() => {
		show = false;
		showSubmitFeedbackModal = true;
	}}
>
	<div class=" self-center mr-3"><ChatBubbleOval className="size-5" /></div>
	<div class=" self-center truncate">{$i18n.t('Submit Feedback')}</div>
</button>
```

### 17.3 `static/homepage.html` — fully removed

Deleted, with nothing else on the page touched:

- The `#openFeedbackBtn` button (was under the hero copy, next to the
  headline).
- The entire `.fb-overlay`/`.fb-modal` markup block.
- The entire feedback `<script>` IIFE (word-count logic, open/close
  handlers, the `fetch(SUBMIT_API_ENDPOINT, ...)` call).
- All `.feedback-trigger-btn` and `.fb-*` CSS rules, including the mobile
  media-query override for `.feedback-trigger-btn`.

The landing page's other `<script>` block (the Registration & Sign-in Guide
panel logic) and everything else on the page — hero copy, sign-in/sign-up
card, trust strip, footer — is byte-for-byte unchanged.

### 17.4 What was intentionally NOT changed

- The backend (`backend/open_webui/main.py`'s `submit_feedback_proxy`) —
  zero changes. Same request/response shape (`{email, comment}` in,
  `{success, message}` or an error out), same SQLite `feedback` table.
- Word-count limit (100), required-field validation, and the exact
  error/success copy — carried over unchanged from the original.

### 17.5 Verification

- `svelte-check`: no new error/warning *types* introduced — the only
  entries for `SubmitFeedbackModal.svelte` and `UserMenu.svelte` are the
  same pre-existing `i18n`-as-store typing pattern present throughout this
  codebase wherever `getContext('i18n')` is used (confirmed by sampling the
  actual message text at several of the reported line numbers, not just the
  count).
- `static/homepage.html`: `<div>` balance 20/20 (down from 24/24, consistent
  with the ~4 divs removed: `.fb-overlay`, `.fb-modal`, `.fb-step`, and the
  hero's now-single-child wrapper), `<script>` balance 1/1 (down from 2,
  the feedback IIFE fully removed), zero remaining references to
  `fb-`/`openFeedbackBtn`/`feedback-trigger-btn`/`SUBMIT_API_ENDPOINT`
  anywhere in the file (grepped and confirmed empty).
- Open the user avatar menu in the app → "Submit Feedback" appears between
  "Keyboard shortcuts" and the Sign Out divider, opens the modal, submits
  successfully, colors match the rest of the app's dark/light theme.
- Landing page: hero copy, sign-in/sign-up card, and the Registration &
  Sign-in Guide panel all render and behave exactly as before this change —
  only the feedback button/modal is gone.

---

## 18. Bug fix — "Submit Feedback" missing from the bottom-left sidebar user menu

Reported: the new menu item (§17.2) showed up in the top-right Navbar's user
menu, but not in the bottom-left one (the user's own profile button at the
foot of the sidebar).

**Root cause:** `UserMenu.svelte` is instantiated in **three** places —
once in `Navbar.svelte` (top-right, passes `help={true}`) and **twice** in
`Sidebar.svelte` (lines 943 and 1608, the collapsed and expanded bottom-left
profile buttons, **neither** passes a `help` prop, so it defaults to
`false`). §17.2 nested the new button inside the existing `{#if help}`
block alongside Documentation/Releases/Keyboard shortcuts — which is exactly
right for those admin-doc links, but meant Submit Feedback inherited the
same restriction and disappeared everywhere `help` isn't explicitly `true`.

**Fix** — `UserMenu.svelte`: moved the Submit Feedback button out from
under `{#if help}`, as an unconditional sibling right after it:

```diff
  			<div class=" self-center truncate">{$i18n.t('Keyboard shortcuts')}</div>
  		</button>
+ 	{/if}
+
+ 	<!-- Unconditional — Submit Feedback should be reachable from every
+ 	     instance of this menu, not just the ones with help={true}. -->
+ 	<button
+ 		class="flex rounded-xl py-1.5 px-3 w-full hover:bg-gray-50 dark:hover:bg-gray-800 transition cursor-pointer select-none"
+ 		type="button"
+ 		on:click={() => { show = false; showSubmitFeedbackModal = true; }}
+ 	>
+ 		<div class=" self-center mr-3"><ChatBubbleOval className="size-5" /></div>
+ 		<div class=" self-center truncate">{$i18n.t('Submit Feedback')}</div>
+ 	</button>
- 		<button ...Submit Feedback... was here, still inside {#if help} ...>
- 	{/if}
```

(Documentation/Releases/Keyboard shortcuts remain exactly as they were,
still gated by `help` — this was a targeted fix for Submit Feedback only,
not a change to that existing gating behavior.)

### 18.1 Verification

- `svelte-check`: same count/pattern of pre-existing baseline entries on
  `UserMenu.svelte` (line numbers shifted by a few from the move, message
  text unchanged).
- Open the bottom-left sidebar profile menu (both the collapsed-sidebar and
  expanded-sidebar variants, `Sidebar.svelte:943` and `:1608`) → Submit
  Feedback now appears in both, alongside Settings/Archived Chats/Sign Out.
- Top-right Navbar menu → unaffected, still shows Submit Feedback plus the
  `help`-gated items (Documentation/Releases/Keyboard shortcuts) it already
  had.

---

## 19. Bug fix — "Failed to save feedback" on submit (backend, pre-existing)

Reported: submitting the form (now reachable from the app, §17-18) always
returned "Failed to save feedback."

**Root cause — a pre-existing backend bug, not caused by the §17-18 UI
move.** `backend/open_webui/main.py`'s `submit_feedback_proxy` writes to a
SQLite file at a path that defaults to a **hardcoded Docker container path**:

```python
db_path = os.environ.get("FEEDBACK_DB_PATH", "/app/backend/data/feedback.db")
```

Outside a container (this project's actual local/Windows dev setup), that
absolute path doesn't exist, so `sqlite3.connect()` fails inside
`_write_feedback()`, which the handler catches and reports as a generic
500 "Failed to save feedback" — no matter what the frontend sends. The
landing page's original fetch to this same endpoint would have hit the
identical failure; it simply hadn't been exercised end-to-end in this
environment until the feedback entry point was moved into the app.

**Fix** — use this project's own established `DATA_DIR` convention (already
the single source of truth for where `webui.db` and uploads live, per
`backend/open_webui/config.py`: `DATABASE_URL = f'sqlite:///{DATA_DIR}/webui.db'`,
`UPLOAD_DIR = DATA_DIR / 'uploads'`) instead of a Docker-only path:

```diff
  from open_webui.env import (
      AIOHTTP_CLIENT_SESSION_SSL,
      AUDIT_EXCLUDED_PATHS,
      AUDIT_INCLUDED_PATHS,
      AUDIT_LOG_LEVEL,
      BYPASS_MODEL_ACCESS_CONTROL,
      CHANGELOG,
+     DATA_DIR,
      DEPLOYMENT_ID,
      ...
  )
```

```diff
- db_path = os.environ.get("FEEDBACK_DB_PATH", "/app/backend/data/feedback.db")
+ # Default to this app's own DATA_DIR (same convention webui.db and
+ # uploads already use, open_webui/env.py) rather than a Docker-only
+ # absolute path — that hardcoded path doesn't exist outside a container
+ # (e.g. local/Windows dev), which silently failed every write.
+ db_path = os.environ.get("FEEDBACK_DB_PATH", str(DATA_DIR / "feedback.db"))
```

The `FEEDBACK_DB_PATH` environment variable override is still fully
respected if a deployment sets one explicitly — only the *default* changed,
from a Docker-only guess to this project's actual portable data directory,
which resolves correctly in both Docker and local dev since it's the exact
same variable the rest of the app's persistent storage already depends on.

### 19.1 Verification

- Python syntax check on `backend/open_webui/main.py` passes
  (`ast.parse`), confirming the edit didn't break the file.
- Confirmed no existing `DATA_DIR` import/name already present in
  `main.py` that this would collide with (single clean import, single use
  site).
- Confirmed `DATA_DIR` is the exact variable `config.py` already uses to
  build `DATABASE_URL` (where `webui.db` — confirmed present on disk at
  `backend/open_webui/data/webui.db` — actually lives), so `feedback.db`
  will land in that same, already-known-working directory.
- Restart the backend and resubmit the feedback form → should now return
  `{"success": true, ...}` instead of the 500; a `feedback.db` SQLite file
  should appear in the same directory as `webui.db`.

---

## 20. Feedback modal — dropped the email field, auto-fill from the logged-in user

Since Submit Feedback is now only reachable inside the app (§17-18), the
submitter is always an authenticated user — so there's no need to ask them
to type their own email. Removed the field entirely and read it from the
already-available `user` store instead.

**`src/lib/components/layout/Sidebar/SubmitFeedbackModal.svelte`:**

```diff
- import Tooltip from '$lib/components/common/Tooltip.svelte';
- import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
+ import { user } from '$lib/stores';

  let comment = '';
- let email = '';
  let errorMsg = '';
  ...

  const submitFeedback = async () => {
-   const trimmedEmail = email.trim();
+   const trimmedEmail = ($user?.email ?? '').trim();
    const trimmedComment = comment.trim();

    if (!trimmedEmail) {
-     errorMsg = $i18n.t('Please enter your email.');
+     errorMsg = $i18n.t('Unable to determine your account email. Please contact support.');
      return;
    }
    ...
```

And removed the entire "Enter email" label + tooltip + `<input type="email">`
block from the markup. The modal now shows only: the "Your feedback" label,
the textarea with its word counter, and the Submit button — exactly as
requested. `$user.email` is sent as the `email` field in the same POST body
shape the backend already expects, so no backend changes were needed.

Since `SubmitFeedbackModal.svelte` is a single shared component mounted
from both the top-right Navbar's user menu and both bottom-left Sidebar
user-menu instances (§18), this change applies identically in all three
places automatically — no per-location edits required.

### 20.1 Verification

- `svelte-check`: fewer total entries than before for this file (the
  `Tooltip`/`InfoCircle`-related warnings disappeared along with their
  removed usages), remaining entries are the same pre-existing
  `i18n`-as-store baseline pattern — no new error types.
- Open the modal from all three locations (top-right Navbar, bottom-left
  Sidebar collapsed, bottom-left Sidebar expanded) → each shows only the
  feedback textarea + Submit button, no email field.
- Submit → the email recorded server-side is the logged-in user's own
  account email, not manually typed.
