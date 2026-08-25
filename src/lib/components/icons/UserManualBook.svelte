<script lang="ts">
	export let className = 'size-4';
	export let strokeWidth = '1.5';
</script>

<svg
	aria-hidden="true"
	xmlns="http://www.w3.org/2000/svg"
	fill="none"
	viewBox="0 0 24 24"
	stroke-width={strokeWidth}
	stroke="currentColor"
	class={className}
>
	<!-- Closed book with a curved spine -->
	<path stroke-linecap="round" stroke-linejoin="round" d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
	<path
		stroke-linecap="round"
		stroke-linejoin="round"
		d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"
	/>
	<!-- Info badge on the cover -->
	<circle cx="12.5" cy="9.1" r="2.35" stroke-linecap="round" stroke-linejoin="round" />
	<line x1="12.5" y1="8.6" x2="12.5" y2="10.2" stroke-linecap="round" />
	<circle cx="12.5" cy="7.75" r="0.35" fill="currentColor" stroke="none" />
</svg>
