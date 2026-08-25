<script lang="ts">
	import { getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { user } from '$lib/stores';

	export let show = false;

	const i18n = getContext('i18n');

	const MAX_WORDS = 100;

	let comment = '';
	let errorMsg = '';
	let successMsg = '';
	let submitting = false;

	const getWords = (text: string) => {
		const trimmed = text.trim();
		return trimmed.length ? trimmed.split(/\s+/) : [];
	};

	$: wordCount = getWords(comment).length;

	const handleCommentInput = () => {
		const words = getWords(comment);
		if (words.length > MAX_WORDS) {
			comment = words.slice(0, MAX_WORDS).join(' ');
		}
	};

	const reset = () => {
		comment = '';
		errorMsg = '';
		successMsg = '';
		submitting = false;
	};

	// Reset the form every time the modal is (re)opened.
	$: if (show) reset();

	const submitFeedback = async () => {
		errorMsg = '';
		successMsg = '';

		const trimmedEmail = ($user?.email ?? '').trim();
		const trimmedComment = comment.trim();

		if (!trimmedEmail) {
			errorMsg = $i18n.t('Unable to determine your account email. Please contact support.');
			return;
		}
		if (!trimmedComment) {
			errorMsg = $i18n.t('Please write some feedback before submitting.');
			return;
		}
		if (getWords(trimmedComment).length > MAX_WORDS) {
			errorMsg = $i18n.t('Feedback cannot exceed {{maxWords}} words.', { maxWords: MAX_WORDS });
			return;
		}

		submitting = true;
		try {
			// Not under WEBUI_API_BASE_URL (.../api/v1) — this is the
			// top-level /api/submit-feedback route, same one the landing
			// page used to call.
			const res = await fetch(`${WEBUI_BASE_URL}/api/submit-feedback`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					...(localStorage.token && { authorization: `Bearer ${localStorage.token}` })
				},
				credentials: 'include',
				body: JSON.stringify({ email: trimmedEmail, comment: trimmedComment })
			});

			if (!res.ok) {
				const errData = await res.json().catch(() => ({}));
				throw new Error(errData.detail || errData.error || 'Submission failed');
			}

			successMsg = $i18n.t('Thank you! Your feedback has been submitted.');
			setTimeout(() => {
				show = false;
			}, 1200);
		} catch (err: any) {
			errorMsg = err?.message || $i18n.t('Failed to submit feedback. Please try again.');
		} finally {
			submitting = false;
		}
	};
</script>

<Modal bind:show size="sm">
	<div>
		<div class=" flex justify-between dark:text-gray-300 px-5 pt-4 pb-0.5">
			<div class=" text-lg font-medium self-center">{$i18n.t('Submit Feedback')}</div>
			<button
				class="self-center"
				aria-label={$i18n.t('Close')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className={'size-5'} />
			</button>
		</div>

		<div class="px-5 pt-4 pb-5 w-full flex flex-col gap-3">
			<div>
				<label
					class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1 block"
					for="feedback-comment"
				>
					{$i18n.t('Your feedback')}
				</label>
				<textarea
					id="feedback-comment"
					class="w-full resize-none text-sm bg-transparent border border-gray-100 dark:border-gray-800 rounded-xl px-3 py-2 outline-hidden focus:border-gray-300 dark:focus:border-gray-700 transition"
					rows="5"
					placeholder={$i18n.t('Write your feedback here...')}
					bind:value={comment}
					on:input={handleCommentInput}
				></textarea>
				<p class="text-xs text-gray-400 dark:text-gray-500 text-right mt-1">
					{wordCount} / {MAX_WORDS} {$i18n.t('words')}
				</p>
			</div>

			{#if errorMsg}
				<p class="text-xs text-red-500">{errorMsg}</p>
			{/if}
			{#if successMsg}
				<p class="text-xs text-emerald-600 dark:text-emerald-500">{successMsg}</p>
			{/if}

			<div class="flex justify-end pt-1">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
					disabled={submitting}
					on:click={submitFeedback}
				>
					{submitting ? $i18n.t('Submitting...') : $i18n.t('Submit')}
				</button>
			</div>
		</div>
	</div>
</Modal>
