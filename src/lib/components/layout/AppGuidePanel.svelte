<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { appGuideWidth, mobile, showAppGuide } from '$lib/stores';

	const i18n = getContext('i18n');

	// Lazy-load the PDF into the iframe only once, the first time the panel
	// is opened — not on initial app load.
	let frameSrc = '';
	$: if ($showAppGuide && !frameSrc) {
		// Explicit #zoom=100 — an absolute value, not a "fit to container"
		// keyword. Chrome's built-in PDF viewer remembers the last zoom it
		// computed across documents in the same browser session, so a
		// relative fit (page-width/FitH) in one panel can bleed into the
		// next PDF opened afterward, even in a differently-sized panel. An
		// explicit percentage avoids that entirely.
		frameSrc = '/static/user-manual-app.pdf#zoom=100';
	}

	const close = () => {
		showAppGuide.set(false);
	};

	const handleKeydown = (e: KeyboardEvent) => {
		if (e.key === 'Escape' && $showAppGuide) {
			close();
		}
	};

	// --- Drag-to-resize (left edge) — same mousedown/mousemove/mouseup
	// pattern as Sidebar.svelte's own resizer, but dragging left grows the
	// panel (it's anchored to the right) instead of shrinking it. ---
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
		const dx = startClientX - clientX;
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
</script>

<svelte:window
	on:keydown={handleKeydown}
	on:mousemove={(e) => {
		if (!isResizing) return;
		resizeHandler(e.clientX);
	}}
	on:mouseup={resizeEndHandler}
/>

<!-- Real layout sibling (not an overlay) — takes actual flex width next to
     the sidebar/chat content, so opening/closing it resizes the rest of the
     app instead of floating on top of it. Placed as a flex sibling inside
     src/routes/(app)/+layout.svelte's main row, after the content wrapper,
     so every authenticated route (chat, workspace, notes, admin, ...) gets
     the same split-screen behavior for free. -->
<div
	class="h-full shrink-0 overflow-hidden bg-gray-50 dark:bg-gray-950 flex flex-row {$showAppGuide
		? 'shadow-[-12px_0_40px_rgba(0,0,0,0.25)] dark:shadow-[-12px_0_40px_rgba(0,0,0,0.35)]'
		: ''} {isResizing ? '' : 'transition-[width] duration-500 ease-in-out'}"
	style="width: {$showAppGuide ? ($mobile ? '100%' : `${$appGuideWidth}px`) : '0px'};"
	aria-hidden={!$showAppGuide}
>
	{#if $showAppGuide && !$mobile}
		<!-- Drag handle — grab this edge to resize the panel. Invisible at
		     rest (matches the rest of the app's own resize handle, e.g.
		     Sidebar.svelte's #sidebar-resizer) with only a border-color shift
		     on hover/drag for feedback, so it reads as part of the panel's
		     own edge rather than a separate highlighted bar. The generous
		     -left-2/-right-2 hit-zone (vs. Sidebar's original 3px) is kept —
		     that width was never the problem, only the always-visible fill
		     color was. -->
		<div
			class="relative w-1.5 shrink-0 h-full cursor-col-resize border-l border-transparent hover:border-gray-300 dark:hover:border-gray-700 transition-colors {isResizing
				? '!border-orange-400 dark:!border-orange-400'
				: ''}"
			on:mousedown={resizeStartHandler}
			role="separator"
			aria-label={$i18n.t('Resize user guide panel')}
		>
			<div class="absolute -left-2 -right-2 top-0 bottom-0"></div>
		</div>
	{/if}
	<div class="flex-1 min-w-0 h-full flex flex-col">
		<div
			class="flex items-center justify-between gap-4 px-6 py-4 border-b border-gray-100 dark:border-white/10 bg-white dark:bg-black/20 shrink-0"
		>
			<h3 class="text-gray-800 dark:text-white font-semibold text-base truncate">
				{$i18n.t('Bharat AI Platform — User Guide')}
			</h3>
			<button
				type="button"
				class="w-8 h-8 shrink-0 rounded-full bg-red-500 hover:bg-red-600 text-white flex items-center justify-center text-xl leading-none transition-colors duration-150"
				on:click={close}
				aria-label={$i18n.t('Close user guide')}
			>
				&times;
			</button>
		</div>
		<div class="flex-1 min-h-0 bg-gray-100 dark:bg-gray-950 relative">
			{#if frameSrc}
				<iframe
					title="Bharat AI Platform User Guide"
					src={frameSrc}
					class="w-full h-full border-0 block"
				></iframe>
			{/if}
			{#if isResizing}
				<!-- The PDF iframe is a separate document — if the cursor is over
				     it when the mouse button is released, mouseup fires inside
				     the iframe's own document and never reaches our
				     `<svelte:window>` listener, leaving isResizing stuck true
				     forever (the panel then keeps following the mouse). This
				     transparent overlay sits above the iframe only while
				     actively dragging, so mouse events never reach it at all —
				     they stay on our page, where the window-level handlers can
				     see them. -->
				<div class="absolute inset-0 z-10 cursor-col-resize"></div>
			{/if}
		</div>
	</div>
</div>
