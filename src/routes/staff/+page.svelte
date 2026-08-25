<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';

	import { toast } from 'svelte-sonner';

	import { onMount, getContext, tick } from 'svelte';
	import { fly, fade } from 'svelte/transition';
	import { cubicOut, quintOut } from 'svelte/easing';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { getBackendConfig } from '$lib/apis';
	import {
		ldapUserSignIn,
		getSessionUser,
		userSignIn,
		userSignUp,
		completeUserProfile,
		updateUserTimezone
	} from '$lib/apis/auths';

	import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
	import { WEBUI_NAME, config, user, socket } from '$lib/stores';

	import { generateInitialsImage, canvasPixelTest, getUserTimezone } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import OnBoarding from '$lib/components/OnBoarding.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import { redirect } from '@sveltejs/kit';

	const i18n = getContext('i18n');

	let loaded = false;
	let loggingIn = false;

	let mode = $config?.features.enable_ldap ? 'ldap' : 'signin';

	// This page (/staff) is the internal-only credential sign-in page — it
	// opens straight on the email/password form rather than the public
	// chooser (the "Back" button still lets you reach the chooser manually).
	let showLoginForm = true;

	let form = null;

	let name = '';
	let email = '';
	let password = '';
	let confirmPassword = '';
	let termsAccepted = false;
	let showTermsModal = false;
	let hasScrolledToBottom = false;

	let department = '';
	let designation = '';
	let mobileNumber = '';
	// Set when completing a profile after a first-time Parichay/OAuth login —
	// the session user we hold off logging in until the extra fields are submitted.
	let pendingOauthUser: any = null;

	let ldapUsername = '';

	// --- Server-side CAPTCHA state ---
	let captchaToken = '';
	let captchaImageUrl = '';
	let captchaInputValue = '';
	let captchaVerified = false;
	let captchaStatus = '';
	let captchaStatusType = ''; // 'success' | 'error'

	async function refreshCaptcha() {
		captchaInputValue = '';
		captchaVerified = false;
		captchaStatus = '';
		captchaStatusType = '';
		try {
			const res = await fetch(`${WEBUI_API_BASE_URL}/auths/captcha`);
			if (res.ok) {
				const data = await res.json();
				captchaToken = data.token;
				captchaImageUrl = data.image;
			}
		} catch (e) {
			console.error('Failed to load captcha', e);
		}
	}

	function verifyCaptcha() {
		const userInput = captchaInputValue.trim();

		if (!userInput) {
			captchaStatus = $i18n.t('Please enter the text you see above.');
			captchaStatusType = 'error';
			return;
		}

		// Actual validation is done server-side on submit.
		// Here we only confirm the user has typed something before enabling the submit button.
		captchaVerified = true;
		captchaStatus = $i18n.t('Click sign in to verify.');
		captchaStatusType = 'success';
	}

	const REDIRECT_ALLOWED_HOST_SUFFIX = '.bharatai.gov.in';

	const navigateToRedirect = (target: string | null) => {
		const raw = target || '/';

		// In-app path ('//host' is protocol-relative, i.e. external — exclude it)
		if (raw.startsWith('/') && !raw.startsWith('//')) {
			goto(raw);
			return;
		}

		try {
			const url = new URL(raw);
			if (
				(url.protocol === 'https:' || url.protocol === 'http:') &&
				(url.hostname === REDIRECT_ALLOWED_HOST_SUFFIX.slice(1) ||
					url.hostname.endsWith(REDIRECT_ALLOWED_HOST_SUFFIX))
			) {
				window.location.href = url.href;
				return;
			}
		} catch {
			// Not a parseable absolute URL — fall through to home
		}

		goto('/');
	};

	const setSessionUser = async (sessionUser, redirectPath: string | null = null) => {
		if (sessionUser) {
			console.log(sessionUser);
			toast.success($i18n.t(`You're now logged in.`));
			if (sessionUser.token) {
				localStorage.token = sessionUser.token;
			}
			$socket.emit('user-join', { auth: { token: sessionUser.token } });
			await user.set(sessionUser);
			await config.set(await getBackendConfig());

			// Update user timezone
			const timezone = getUserTimezone();
			if (sessionUser.token && timezone) {
				updateUserTimezone(sessionUser.token, timezone);
			}

			if (!redirectPath) {
				redirectPath = $page.url.searchParams.get('redirect') || '/';
			}

			localStorage.removeItem('redirectPath');
			navigateToRedirect(redirectPath);
		}
	};

	const signInHandler = async () => {
		const sessionUser = await userSignIn(email, password, captchaToken, captchaInputValue).catch(
			async (error) => {
				toast.error(`${error}`);
				loggingIn = false;
				// Refresh captcha so the user gets a new challenge after a failed attempt
				await refreshCaptcha();
				captchaVerified = false;
				return null;
			}
		);

		if (sessionUser) {
			await setSessionUser(sessionUser);
		} else {
			loggingIn = false;
		}
	};

	const signUpHandler = async () => {
		if (!termsAccepted) {
			toast.error($i18n.t('Please read and accept the Terms and Conditions to proceed.'));
			loggingIn = false;
			return;
		}

		if ($config?.features?.enable_signup_password_confirmation) {
			if (password !== confirmPassword) {
				toast.error($i18n.t('Passwords do not match.'));
				loggingIn = false;
				return;
			}
		}

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

		if (sessionUser) {
			await setSessionUser(sessionUser);
		} else {
			loggingIn = false;
		}
	};

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
			// Role is still 'pending' at this point — the app shell's AccountPending
			// gate takes over from here until an admin approves the account.
			await setSessionUser(pendingOauthUser, '/');
		} else {
			loggingIn = false;
		}
	};

	const ldapSignInHandler = async () => {
		const sessionUser = await ldapUserSignIn(ldapUsername, password).catch((error) => {
			toast.error(`${error}`);
			loggingIn = false;
			return null;
		});
		if (sessionUser) {
			await setSessionUser(sessionUser);
		} else {
			loggingIn = false;
		}
	};

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

	const oauthCallbackHandler = async () => {
		// Get the value of the 'token' cookie
		function getCookie(name) {
			const match = document.cookie.match(
				new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)')
			);
			return match ? decodeURIComponent(match[1]) : null;
		}

		const token = getCookie('token');
		if (!token) {
			return;
		}

		const sessionUser = await getSessionUser(token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (!sessionUser) {
			return;
		}

		localStorage.token = token;

		const newUserFlag = $page.url.searchParams.get('new_user');
		if (newUserFlag === '1') {
			// First-time Parichay/OAuth login: the backend has already provisioned
			// this account with role=pending. Don't log them into the app yet —
			// collect department/designation/mobile number first.
			showLoginForm = true;
			mode = 'signup-complete';
			name = sessionUser.name || $page.url.searchParams.get('name') || '';
			email = sessionUser.email || $page.url.searchParams.get('email') || '';
			pendingOauthUser = sessionUser;
			return;
		}

		await setSessionUser(sessionUser, localStorage.getItem('redirectPath') || null);
	};

	let onboarding = false;

	async function setLogoImage() {
		await tick();
		const logo = document.getElementById('logo');

		if (logo) {
			const isDarkMode = document.documentElement.classList.contains('dark');

			if (isDarkMode) {
				const darkImage = new Image();
				darkImage.src = `${WEBUI_BASE_URL}/static/favicon-dark.png`;

				darkImage.onload = () => {
					logo.src = `${WEBUI_BASE_URL}/static/favicon-dark.png`;
					logo.style.filter = ''; // Ensure no inversion is applied if favicon-dark.png exists
				};

				darkImage.onerror = () => {
					logo.style.filter = 'invert(1)'; // Invert image if favicon-dark.png is missing
				};
			}
		}
	}

	onMount(async () => {
		const redirectPath = $page.url.searchParams.get('redirect');
		if ($user !== undefined) {
			navigateToRedirect(redirectPath);
		} else {
			if (redirectPath) {
				localStorage.setItem('redirectPath', redirectPath);
			}
		}

		const error = $page.url.searchParams.get('error');
		if (error) {
			toast.error(error);
		}

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
	});
</script>

<svelte:head>
	<title>
		{`${$WEBUI_NAME}`}
	</title>
</svelte:head>

<OnBoarding
	bind:show={onboarding}
	getStartedHandler={() => {
		onboarding = false;
		mode = $config?.features.enable_ldap ? 'ldap' : 'signup';
	}}
/>

<div
	class="w-full min-h-screen flex items-center justify-center p-4 md:p-8 text-white relative overflow-hidden"
	id="auth-page"
>
	<!-- Ambient Background Glows (Sovereign Tech theme) -->
	<div
		class="w-full h-full absolute top-0 left-0 bg-[#06070b] transition-colors duration-300 pointer-events-none"
	>
		<!-- Dotted overlay pattern -->
		<div class="absolute inset-0 bg-dot-pattern opacity-40"></div>

		<!-- Saffron Glow (Top-Right) -->
		<div
			class="absolute top-[-10%] right-[-10%] w-[60%] h-[60%] rounded-full bg-orange-600/12 blur-[140px] animate-pulse-slow"
		></div>
		<!-- Emerald Glow (Bottom-Left) -->
		<div
			class="absolute bottom-[-10%] left-[-10%] w-[60%] h-[60%] rounded-full bg-emerald-600/8 blur-[140px] animate-pulse-slow-reverse"
		></div>
		<!-- Deep Indigo Glow (Center-Left) -->
		<div
			class="absolute top-[25%] left-[20%] w-[50%] h-[50%] rounded-full bg-indigo-600/10 blur-[130px] animate-pulse-slow"
		></div>
	</div>

	<div class="w-full absolute top-0 left-0 right-0 h-8 drag-region z-50" />

	{#if loaded}
		<!-- Main Floating Glass Container -->
		<div
			class="w-full max-w-5xl min-h-[600px] md:min-h-[680px] max-h-[calc(100vh-2rem)] md:max-h-[calc(100vh-4rem)] my-auto rounded-3xl overflow-hidden border border-white/10 bg-white/[0.03] backdrop-blur-2xl shadow-[0_25px_50px_-12px_rgba(0,0,0,0.5)] flex flex-col md:flex-row z-10"
		>
			{#if ($config?.features.auth_trusted_header ?? false) || $config?.features.auth === false}
				<div class="w-full h-full flex flex-col justify-center items-center bg-transparent">
					<div class="my-auto pb-10 w-full max-w-md text-center">
						<div
							class="flex items-center justify-center gap-3 text-xl sm:text-2xl text-center font-medium text-gray-200"
						>
							<div>
								{$i18n.t('Signing in to {{WEBUI_NAME}}', { WEBUI_NAME: $WEBUI_NAME })}
							</div>

							<div>
								<Spinner className="size-5" />
							</div>
						</div>
					</div>
				</div>
			{:else}
				<!-- Left Panel (Info & Highlights) -->
				<div
					class="w-full md:w-[55%] min-h-full flex flex-col justify-between p-6 md:p-8 border-b md:border-b-0 md:border-r border-white/10 relative overflow-hidden bg-white/[0.01]"
				>
					<!-- Dotted overlay pattern inside left panel -->
					<div class="absolute inset-0 bg-dot-pattern opacity-20 pointer-events-none"></div>

					<!-- Top branding logo/text -->
					<div class="z-10 flex items-center gap-3">
						<span
							class="text-xl font-bold bg-linear-to-r from-orange-500 to-amber-500 bg-clip-text text-transparent tracking-wider"
							>Bharat AI Platform</span
						>
					</div>

					<!-- Center content: Animated India AI Logo -->
					<div class="z-10 my-auto flex flex-col justify-center items-center w-full py-8">
						<div
							class="bg-white/[0.04] backdrop-blur-md border border-white/10 rounded-3xl p-10 shadow-2xl relative group overflow-hidden max-w-[300px] animate-logo-float"
						>
							<!-- Inner glowing aura -->
							<div
								class="absolute inset-0 bg-radial-to-r from-orange-500/15 to-transparent opacity-100"
							></div>
							<img
								src="/static/india-ai-logo.png"
								alt="India AI Logo"
								class="w-48 h-auto object-contain relative z-10"
							/>
						</div>
						<div class="mt-8 text-center">
							<h1
								class="text-3xl xl:text-4xl font-extrabold tracking-tight leading-tight text-white"
							>
								Empowering India's <span
									class="bg-linear-to-r from-orange-500 via-white to-emerald-500 bg-clip-text text-transparent"
									>AI Future</span
								>
							</h1>
							<p class="text-gray-400 text-xs uppercase tracking-widest font-semibold mt-2">
								National AI Sovereign Platform
							</p>
						</div>
					</div>

					<!-- Footer / Credits inside left panel -->
					<div class="z-10 text-[0.7rem] text-gray-400 flex justify-between items-center w-full">
						<span>© 2026 INDIA AI. All rights reserved.</span>
						<span>Designed and Developed by C-DAC</span>
					</div>
				</div>

				<!-- Right Panel (Login Form) -->
				<div
					class="w-full md:w-[45%] min-h-full flex flex-col items-center p-5 md:p-6 bg-black/10 dark:bg-black/30 backdrop-blur-md relative overflow-y-auto"
				>
					<div class="w-full max-w-md my-auto z-10">
						<!-- Official Logos Row -->
						<div class="flex justify-center items-center gap-4 mb-3">
							<div
								class="bg-white rounded-xl p-2.5 border border-white/20 shadow-md transition-all hover:scale-105 duration-200 flex items-center justify-center h-19 w-34"
							>
								<img
									src="/static/meit-logo.png"
									alt="MeitY Logo"
									class="h-13 w-auto object-contain"
								/>
							</div>
							<div
								class="bg-white rounded-xl p-2.5 border border-white/20 shadow-md transition-all hover:scale-105 duration-200 flex items-center justify-center h-19 w-34"
							>
								<img
									src="/static/india-ai-logo.png"
									alt="IndiaAI Logo"
									class="h-13 w-auto object-contain"
								/>
							</div>
						</div>

						<!-- Title -->
						<div
							class="text-xl font-bold bg-linear-to-r from-orange-400 to-amber-400 bg-clip-text text-transparent text-center mb-3"
						>
							{#if $config?.onboarding ?? false}
								{$i18n.t(`Get started with {{WEBUI_NAME}}`, { WEBUI_NAME: $WEBUI_NAME })}
							{:else if mode === 'ldap'}
								{$i18n.t(`Sign in to {{WEBUI_NAME}} with LDAP`, { WEBUI_NAME: $WEBUI_NAME })}
							{:else if mode === 'signin'}
								{$i18n.t(`Sign in to {{WEBUI_NAME}}`, { WEBUI_NAME: $WEBUI_NAME })}
							{:else if mode === 'signup-complete'}
								{$i18n.t('Complete your profile')}
							{:else}
								{$i18n.t(`Sign up to {{WEBUI_NAME}}`, { WEBUI_NAME: $WEBUI_NAME })}
							{/if}
						</div>

						{#if $config?.onboarding ?? false}
							<div class="mb-2 text-center text-xs font-medium text-gray-400">
								ⓘ {$WEBUI_NAME}
								{$i18n.t(
									'does not make any external connections, and your data stays securely on your locally hosted server.'
								)}
							</div>
						{/if}

						<div class="grid">
						{#if !showLoginForm}
							<!-- Entry buttons: Continue with Parichay / Continue with Open WebUI -->
							<div
								class="col-start-1 row-start-1 flex flex-col space-y-3 mt-2"
								in:fly={{ y: 10, duration: 360, delay: 90, easing: quintOut }}
								out:fly={{ y: -10, duration: 260, easing: quintOut }}
							>
								<button
									type="button"
									class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 hover:border-orange-500/30 active:scale-[0.98] transition-all duration-200 w-full rounded-xl font-medium text-base py-3.5"
									on:click={() => {
										window.location.replace(`${WEBUI_BASE_URL}/oauth/parichay/login`);
									}}
								>
									<svg
										xmlns="http://www.w3.org/2000/svg"
										fill="none"
										viewBox="0 0 24 24"
										stroke-width="1.5"
										stroke="currentColor"
										class="size-6 mr-3"
										aria-hidden="true"
									>
										<path
											stroke-linecap="round"
											stroke-linejoin="round"
											d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.57-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z"
										/>
									</svg>
									<span>{$i18n.t('Continue with {{provider}}', { provider: 'Parichay' })}</span>
								</button>

								<div class="inline-flex items-center justify-center w-full">
									<hr class="w-full h-px border-0 bg-white/10" />
									<span class="px-3 text-xs font-medium text-gray-400 bg-transparent shrink-0"
										>{$i18n.t('OR')}</span
									>
									<hr class="w-full h-px border-0 bg-white/10" />
								</div>

								<button
									type="button"
									class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 hover:border-orange-500/30 active:scale-[0.98] transition-all duration-200 w-full rounded-xl font-medium text-base py-3.5"
									on:click={() => {
										showLoginForm = true;
									}}
								>
									<img
										src="/open-webui-logo.png"
										alt="open-webui-logo"
										class="w-6 h-6 rounded-full mr-3"
									/>
									<span>{$i18n.t('Continue with {{provider}}', { provider: 'Open WebUI' })}</span>
								</button>

								{#if Object.keys($config?.oauth?.providers ?? {}).length > 0}
									<div class="flex flex-col space-y-2 !mt-3">
										{#if $config?.oauth?.providers?.google}
											<button
												class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 transition duration-200 w-full rounded-xl font-medium text-sm py-2.5"
												on:click={() => {
													window.location.href = `${WEBUI_BASE_URL}/oauth/google/login`;
												}}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 48 48"
													class="size-6 mr-3"
													aria-hidden="true"
												>
													<path
														fill="#EA4335"
														d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
													/><path
														fill="#4285F4"
														d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
													/><path
														fill="#FBBC05"
														d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
													/><path
														fill="#34A853"
														d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
													/><path fill="none" d="M0 0h48v48H0z" />
												</svg>
												<span>{$i18n.t('Continue with {{provider}}', { provider: 'Google' })}</span>
											</button>
										{/if}
										{#if $config?.oauth?.providers?.microsoft}
											<button
												class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 transition duration-200 w-full rounded-xl font-medium text-sm py-2.5"
												on:click={() => {
													window.location.href = `${WEBUI_BASE_URL}/oauth/microsoft/login`;
												}}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 21 21"
													class="size-6 mr-3"
													aria-hidden="true"
												>
													<rect x="1" y="1" width="9" height="9" fill="#f25022" /><rect
														x="1"
														y="11"
														width="9"
														height="9"
														fill="#00a4ef"
													/><rect x="11" y="1" width="9" height="9" fill="#7fba00" /><rect
														x="11"
														y="11"
														width="9"
														height="9"
														fill="#ffb900"
													/>
												</svg>
												<span>{$i18n.t('Continue with {{provider}}', { provider: 'Microsoft' })}</span>
											</button>
										{/if}
										{#if $config?.oauth?.providers?.github}
											<button
												class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 transition duration-200 w-full rounded-xl font-medium text-sm py-2.5"
												on:click={() => {
													window.location.href = `${WEBUI_BASE_URL}/oauth/github/login`;
												}}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													viewBox="0 0 24 24"
													class="size-6 mr-3"
													aria-hidden="true"
												>
													<path
														fill="currentColor"
														d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.92 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57C20.565 21.795 24 17.31 24 12c0-6.63-5.37-12-12-12z"
													/>
												</svg>
												<span>{$i18n.t('Continue with {{provider}}', { provider: 'GitHub' })}</span>
											</button>
										{/if}
										{#if $config?.oauth?.providers?.oidc}
											<button
												class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 transition duration-200 w-full rounded-xl font-medium text-sm py-2.5"
												on:click={() => {
													window.location.href = `${WEBUI_BASE_URL}/oauth/oidc/login`;
												}}
											>
												<svg
													xmlns="http://www.w3.org/2000/svg"
													fill="none"
													viewBox="0 0 24 24"
													stroke-width="1.5"
													stroke="currentColor"
													class="size-6 mr-3"
													aria-hidden="true"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M15.75 5.25a3 3 0 0 1 3 3m3 0a6 6 0 0 1-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1 1 21.75 8.25Z"
													/>
												</svg>

												<span
													>{$i18n.t('Continue with {{provider}}', {
														provider: $config?.oauth?.providers?.oidc ?? 'SSO'
													})}</span
												>
											</button>
										{/if}
										{#if $config?.oauth?.providers?.feishu}
											<button
												class="flex justify-center items-center bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 hover:text-white border border-white/10 transition duration-200 w-full rounded-xl font-medium text-sm py-2.5"
												on:click={() => {
													window.location.href = `${WEBUI_BASE_URL}/oauth/feishu/login`;
												}}
											>
												<span>{$i18n.t('Continue with {{provider}}', { provider: 'Feishu' })}</span>
											</button>
										{/if}
									</div>
								{/if}
							</div>
						{:else}
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
							<button
								type="button"
								class="flex items-center text-xs text-gray-400 hover:text-orange-400 transition duration-200 mb-3 -mt-1"
								on:click={() => {
									showLoginForm = false;
								}}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-3.5 mr-1"
									aria-hidden="true"
								>
									<path stroke-linecap="round" stroke-linejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
								</svg>
								{$i18n.t('Back')}
							</button>

							{#if $config?.features.enable_login_form || $config?.features.enable_ldap || form || mode === 'signup-complete'}
								<div class="flex flex-col space-y-2.5">
									{#if mode === 'signup' || mode === 'signup-complete'}
										<div>
											<label
												for="name"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Name')}</label
											>
											<input
												bind:value={name}
												type="text"
												id="name"
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 w-full disabled:opacity-60"
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
											<label
												for="department"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Department')}</label
											>
											<input
												bind:value={department}
												type="text"
												id="department"
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 w-full"
												autocomplete="organization"
												placeholder={$i18n.t('Enter Your Department')}
												required
											/>
										</div>
										<div>
											<label
												for="designation"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Designation')}</label
											>
											<input
												bind:value={designation}
												type="text"
												id="designation"
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 w-full"
												autocomplete="organization-title"
												placeholder={$i18n.t('Enter Your Designation')}
												required
											/>
										</div>
										<div>
											<label
												for="mobile-number"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Mobile Number')}</label
											>
											<input
												bind:value={mobileNumber}
												type="tel"
												id="mobile-number"
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 w-full"
												autocomplete="tel"
												placeholder={$i18n.t('Enter Your Mobile Number')}
												pattern={'[0-9]{10}'}
												required
											/>
										</div>
									{/if}

									{#if mode === 'ldap'}
										<div>
											<label
												for="username"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Username')}</label
											>
											<input
												bind:value={ldapUsername}
												type="text"
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 w-full"
												autocomplete="username"
												name="username"
												id="username"
												placeholder={$i18n.t('Enter Your Username')}
												required
											/>
										</div>
									{:else}
										<div>
											<label
												for="email"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Email')}</label
											>
											<input
												bind:value={email}
												type="email"
												id="email"
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 w-full disabled:opacity-60"
												autocomplete={mode === 'signup' ? 'email' : 'username'}
												name="email"
												placeholder={$i18n.t('Enter Your Email')}
												readonly={mode === 'signup-complete'}
												disabled={mode === 'signup-complete'}
												required
											/>
										</div>
									{/if}

									{#if mode !== 'signup-complete'}
										<div>
											<label
												for="password"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Password')}</label
											>
											<SensitiveInput
												bind:value={password}
												type="password"
												id="password"
												outerClassName="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus-within:border-orange-500 focus-within:ring-1 focus-within:ring-orange-500/20 transition duration-200 flex flex-1"
												inputClassName="w-full text-sm bg-transparent outline-hidden text-white"
												showButtonClassName="pl-1.5 transition text-gray-400 hover:text-orange-500 bg-transparent"
												placeholder={$i18n.t('Enter Your Password')}
												autocomplete={mode === 'signup' ? 'new-password' : 'current-password'}
												name="password"
												screenReader={true}
												required
												aria-required="true"
											/>
										</div>
									{/if}

									{#if mode === 'signup' && $config?.features?.enable_signup_password_confirmation}
										<div>
											<label
												for="confirm-password"
												class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
												>{$i18n.t('Confirm Password')}</label
											>
											<SensitiveInput
												bind:value={confirmPassword}
												type="password"
												id="confirm-password"
												outerClassName="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus-within:border-orange-500 focus-within:ring-1 focus-within:ring-orange-500/20 transition duration-200 flex flex-1"
												inputClassName="w-full text-sm bg-transparent outline-hidden text-white"
												showButtonClassName="pl-1.5 transition text-gray-400 hover:text-orange-500 bg-transparent"
												placeholder={$i18n.t('Confirm Your Password')}
												autocomplete="new-password"
												name="confirm-password"
												required
											/>
										</div>
									{/if}

									{#if mode === 'signup'}
										<div class="mt-1">
											<div class="flex items-start gap-3">
												<div class="relative flex-shrink-0 mt-0.5">
													<input
														type="checkbox"
														id="terms-checkbox"
														bind:checked={termsAccepted}
														disabled={!hasScrolledToBottom}
														class="peer sr-only"
													/>
													<!-- svelte-ignore a11y-click-events-have-key-events -->
													<!-- svelte-ignore a11y-no-static-element-interactions -->
													<div
														class="w-[18px] h-[18px] rounded-[5px] border-2 transition-all duration-200 flex items-center justify-center
														{hasScrolledToBottom
															? 'border-white/20 bg-white/[0.04] cursor-pointer hover:border-orange-400/50'
															: 'border-white/10 bg-white/[0.02] cursor-not-allowed opacity-50'}
														{termsAccepted ? '!bg-orange-500 !border-orange-500' : ''}"
														on:click={() => {
															if (hasScrolledToBottom) {
																termsAccepted = !termsAccepted;
															} else {
																showTermsModal = true;
															}
														}}
													>
														{#if termsAccepted}
															<svg
																class="w-3 h-3 text-white"
																fill="none"
																viewBox="0 0 24 24"
																stroke="currentColor"
																stroke-width="3"
															>
																<path
																	stroke-linecap="round"
																	stroke-linejoin="round"
																	d="M5 13l4 4L19 7"
																/>
															</svg>
														{/if}
													</div>
												</div>
												<span class="text-xs text-gray-400 leading-relaxed select-none">
													{$i18n.t('I agree to the')}
													<button
														type="button"
														class="text-orange-400 hover:text-orange-300 underline underline-offset-2 font-semibold transition-colors duration-150"
														on:click|stopPropagation={() => {
															showTermsModal = true;
														}}
													>
														{$i18n.t('Terms and Conditions')}
													</button>
													{#if !hasScrolledToBottom}
														<span class="text-[10px] text-orange-400/70 italic block mt-1">
															({$i18n.t('Please read the terms first to enable this checkbox')})
														</span>
													{/if}
												</span>
											</div>
										</div>
									{/if}

									<!-- Text CAPTCHA -->
									{#if mode !== 'signup-complete'}
									<div>
										<label
											class="text-xs font-semibold text-gray-300 text-left mb-1.5 block uppercase tracking-wider"
											>{$i18n.t('Verification')}</label
										>
										<!-- svelte-ignore a11y-click-events-have-key-events -->
										<!-- svelte-ignore a11y-no-static-element-interactions -->
										<div
											class="rounded-xl border border-white/10 bg-white/[0.04] overflow-hidden cursor-pointer transition duration-200 hover:border-orange-500/40 w-[170px]"
											on:click={refreshCaptcha}
											title={$i18n.t('Click to refresh')}
										>
											{#if captchaImageUrl}
												<img
													src={captchaImageUrl}
													alt="captcha"
													width="170"
													height="44"
													class="w-full h-auto block select-none pointer-events-none"
												/>
											{:else}
												<div class="w-[170px] h-[44px] bg-slate-800 rounded-xl animate-pulse" />
											{/if}
										</div>

										<div class="flex gap-2 mt-2">
											<input
												bind:value={captchaInputValue}
												type="text"
												autocomplete="off"
												spellcheck="false"
												maxlength="7"
												disabled={captchaVerified}
												placeholder={$i18n.t('Enter the captcha')}
												class="px-4 py-2.5 rounded-xl border border-white/10 bg-white/[0.04] focus:border-orange-500 focus:ring-1 focus:ring-orange-500/20 text-sm outline-hidden text-white transition duration-200 flex-1 disabled:opacity-60"
												on:keydown={(e) => {
													if (e.key === 'Enter') {
														e.preventDefault();
														verifyCaptcha();
													}
												}}
											/>
											<button
												type="button"
												class="p-1.5 shrink-0 rounded-lg text-gray-400 hover:text-orange-400 hover:bg-white/[0.06] transition-colors duration-150"
												on:click|stopPropagation={refreshCaptcha}
												aria-label={$i18n.t('Refresh captcha')}
												title={$i18n.t('Refresh')}
											>
												<svg
													class="w-5 h-5"
													fill="none"
													viewBox="0 0 24 24"
													stroke="currentColor"
													stroke-width="2"
												>
													<path
														stroke-linecap="round"
														stroke-linejoin="round"
														d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"
													/>
												</svg>
											</button>
											<button
												type="button"
												disabled={captchaVerified}
												class="px-4 py-2.5 rounded-xl text-sm font-semibold transition duration-200 shrink-0
												{captchaVerified
													? 'bg-emerald-500/20 text-emerald-400 cursor-default'
													: 'bg-linear-to-r from-orange-600 to-orange-500 hover:from-orange-500 hover:to-orange-400 text-white shadow-lg shadow-orange-500/10'}"
												on:click={verifyCaptcha}
											>
												{captchaVerified ? $i18n.t('Verified') : $i18n.t('Verify')}
											</button>
										</div>

										{#if captchaStatus}
											<div
												class="text-[11px] mt-1.5 {captchaStatusType === 'success'
													? 'text-emerald-400'
													: 'text-orange-400'}"
											>
												{captchaStatus}
											</div>
										{/if}
									</div>
									{/if}
								</div>
							{/if}

							<div class="mt-3">
								{#if $config?.features.enable_login_form || $config?.features.enable_ldap || form || mode === 'signup-complete'}
									{#if mode === 'ldap'}
										<button
											class="bg-linear-to-r from-orange-600 via-orange-500 to-amber-500 hover:from-orange-500 hover:via-orange-400 hover:to-amber-400 text-white font-semibold text-sm py-2.5 w-full rounded-xl shadow-lg shadow-orange-500/25 hover:shadow-xl hover:shadow-orange-500/35 active:scale-[0.98] focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-orange-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-[#06070b] transition-all duration-200"
											type="submit"
										>
											{$i18n.t('Authenticate')}
										</button>
									{:else}
										<button
											class="bg-linear-to-r from-orange-600 via-orange-500 to-amber-500 hover:from-orange-500 hover:via-orange-400 hover:to-amber-400 text-white font-semibold text-sm py-2.5 w-full rounded-xl shadow-lg shadow-orange-500/25 hover:shadow-xl hover:shadow-orange-500/35 active:scale-[0.98] focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-orange-400/60 focus-visible:ring-offset-2 focus-visible:ring-offset-[#06070b] transition-all duration-200"
											type="submit"
										>
											{mode === 'signin'
												? $i18n.t('Sign in')
												: mode === 'signup-complete'
													? $i18n.t('Complete Profile')
													: ($config?.onboarding ?? false)
														? $i18n.t('Create Admin Account')
														: $i18n.t('Create Account')}
										</button>

										{#if $config?.features.enable_signup && !($config?.onboarding ?? false) && mode !== 'signup-complete'}
											<div class=" mt-4 text-xs text-center text-gray-400">
												{mode === 'signin'
													? $i18n.t("Don't have an account?")
													: $i18n.t('Already have an account?')}

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
											</div>
										{/if}
									{/if}
								{/if}
							</div>

							<div class="flex justify-center mt-4">
								<img
									src="/open-webui-logo.png"
									alt="open-webui-logo"
									class="w-10 rounded-full"
								/>
							</div>
						</form>
						{/if}
						</div>

						{#if $config?.features.enable_ldap && $config?.features.enable_login_form}
							<div class="mt-4 text-center">
								<button
									class="text-xs underline text-gray-400 hover:text-orange-400 transition duration-200"
									type="button"
									on:click={() => {
										if (mode === 'ldap')
											mode = ($config?.onboarding ?? false) ? 'signup' : 'signin';
										else mode = 'ldap';
									}}
								>
									<span
										>{mode === 'ldap'
											? $i18n.t('Continue with Email')
											: $i18n.t('Continue with LDAP')}</span
									>
								</button>
							</div>
						{/if}
					</div>
				</div>
			{/if}
		</div>

	{/if}
</div>

<!-- Terms and Conditions Modal -->
{#if showTermsModal}
	<div
		class="fixed inset-0 z-[9999] flex items-center justify-center p-4"
		on:click|self={() => {
			showTermsModal = false;
		}}
		on:keydown={(e) => {
			if (e.key === 'Escape') showTermsModal = false;
		}}
		role="dialog"
		aria-modal="true"
		aria-labelledby="terms-modal-title"
	>
		<div class="absolute inset-0 bg-black/70 backdrop-blur-sm animate-fade-in"></div>

		<div
			class="relative w-full max-w-3xl max-h-[90vh] rounded-2xl border border-white/10 bg-[#0d0f17] shadow-2xl shadow-black/50 flex flex-col animate-modal-scale-in"
		>
			<!-- Header -->
			<div class="flex items-center justify-between px-6 py-4 border-b border-white/10">
				<h2
					id="terms-modal-title"
					class="text-lg font-bold bg-linear-to-r from-orange-400 to-amber-400 bg-clip-text text-transparent"
				>
					{$i18n.t('Terms and Conditions')}
				</h2>
				<button
					type="button"
					class="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors duration-150"
					on:click={() => {
						showTermsModal = false;
					}}
					aria-label="Close"
				>
					<svg
						class="w-5 h-5"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="2"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			<!-- PDF Body -->
			<div
				class="flex-1 overflow-y-auto relative terms-scrollbar"
				style="max-height: 60vh;"
				on:scroll={(e) => {
					const el = e.currentTarget;
					if (el.scrollHeight - el.scrollTop - el.clientHeight < 50) {
						hasScrolledToBottom = true;
					}
				}}
			>
				<object
					data="/static/terms.pdf#toolbar=0"
					type="application/pdf"
					class="w-full border-0"
					style="height: 1200px;"
					title="Terms and Conditions PDF"
				>
					<div class="p-6 text-center text-gray-400">
						<p class="mb-3">{$i18n.t('Unable to display PDF in browser.')}</p>
						<a
							href="/static/terms.pdf"
							target="_blank"
							rel="noopener noreferrer"
							class="text-orange-400 hover:text-orange-300 underline font-semibold"
						>
							{$i18n.t('Download Terms and Conditions PDF')}
						</a>
					</div>
				</object>
				<!-- Scroll sentinel at the bottom -->
				<div class="h-1" id="terms-scroll-sentinel"></div>
			</div>
			{#if !hasScrolledToBottom}
				<div
					class="px-6 py-2 bg-[#0d0f17]/90 border-t border-white/5 flex items-center justify-center"
				>
					<span class="text-[11px] text-orange-400/80 animate-pulse flex items-center gap-1">
						<svg
							class="w-3.5 h-3.5"
							fill="none"
							viewBox="0 0 24 24"
							stroke="currentColor"
							stroke-width="2"
							><path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M19 14l-7 7m0 0l-7-7m7 7V3"
							/></svg
						>
						{$i18n.t('Scroll down to read the full terms')}
					</span>
				</div>
			{/if}

			<!-- Footer -->
			<div class="flex items-center justify-between px-6 py-4 border-t border-white/10">
				<div class="flex items-center gap-2">
					{#if !hasScrolledToBottom}
						<span class="text-[11px] text-gray-500 italic"
							>{$i18n.t('Read the complete document to proceed')}</span
						>
					{:else}
						<span class="text-[11px] text-emerald-400 flex items-center gap-1">
							<svg
								class="w-3.5 h-3.5"
								fill="none"
								viewBox="0 0 24 24"
								stroke="currentColor"
								stroke-width="2"
								><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" /></svg
							>
							{$i18n.t('Terms reviewed')}
						</span>
					{/if}
				</div>
				<div class="flex items-center gap-3">
					<button
						type="button"
						class="px-5 py-2 text-sm font-medium text-gray-400 hover:text-white rounded-xl border border-white/10 hover:bg-white/[0.06] transition-all duration-200"
						on:click={() => {
							showTermsModal = false;
						}}
					>
						{$i18n.t('Close')}
					</button>
					<button
						type="button"
						class="px-5 py-2 text-sm font-semibold rounded-xl shadow-lg transition-all duration-200
						{hasScrolledToBottom
							? 'text-white bg-linear-to-r from-orange-600 to-orange-500 hover:from-orange-500 hover:to-orange-400 shadow-orange-500/10 hover:shadow-orange-500/20 cursor-pointer'
							: 'text-gray-500 bg-white/[0.06] border border-white/10 cursor-not-allowed'}"
						disabled={!hasScrolledToBottom}
						on:click={() => {
							if (hasScrolledToBottom) {
								termsAccepted = true;
								hasScrolledToBottom = true;
								showTermsModal = false;
							}
						}}
					>
						{$i18n.t('I Accept')}
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	@keyframes pulse-slow {
		0%,
		100% {
			opacity: 0.8;
			transform: scale(1);
		}
		50% {
			opacity: 1;
			transform: scale(1.06);
		}
	}
	.animate-pulse-slow {
		animation: pulse-slow 8s ease-in-out infinite;
	}
	@keyframes pulse-slow-reverse {
		0%,
		100% {
			opacity: 0.6;
			transform: scale(1.05) translateY(-10px);
		}
		50% {
			opacity: 0.9;
			transform: scale(0.95) translateY(10px);
		}
	}
	.animate-pulse-slow-reverse {
		animation: pulse-slow-reverse 10s ease-in-out infinite;
	}
	.bg-dot-pattern {
		background-image: radial-gradient(rgba(249, 115, 22, 0.08) 1.5px, transparent 1.5px);
		background-size: 28px 28px;
	}
	@keyframes logo-float {
		0%,
		100% {
			transform: translateY(0);
			filter: drop-shadow(0 5px 15px rgba(249, 115, 22, 0.2));
		}
		50% {
			transform: translateY(-10px);
			filter: drop-shadow(0 20px 35px rgba(249, 115, 22, 0.45));
		}
	}
	.animate-logo-float {
		animation: logo-float 5s ease-in-out infinite;
	}

	/* Terms Modal Animations */
	@keyframes fade-in {
		from {
			opacity: 0;
		}
		to {
			opacity: 1;
		}
	}
	.animate-fade-in {
		animation: fade-in 0.2s ease-out;
	}
	@keyframes modal-scale-in {
		from {
			opacity: 0;
			transform: scale(0.95) translateY(10px);
		}
		to {
			opacity: 1;
			transform: scale(1) translateY(0);
		}
	}
	.animate-modal-scale-in {
		animation: modal-scale-in 0.25s ease-out;
	}
	.terms-scrollbar::-webkit-scrollbar {
		width: 6px;
	}
	.terms-scrollbar::-webkit-scrollbar-track {
		background: transparent;
	}
	.terms-scrollbar::-webkit-scrollbar-thumb {
		background: rgba(255, 255, 255, 0.1);
		border-radius: 3px;
	}
	.terms-scrollbar::-webkit-scrollbar-thumb:hover {
		background: rgba(255, 255, 255, 0.2);
	}
</style>