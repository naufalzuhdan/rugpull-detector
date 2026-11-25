// main.js — RugScan global JS utilities

// Highlight active nav link
document.addEventListener('DOMContentLoaded', () => {
  // Add smooth page transitions
  document.querySelectorAll('a.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
      // Allow normal navigation
    });
  });

  // Auto-resize font-mono inputs
  document.querySelectorAll('.form-input').forEach(input => {
    input.addEventListener('input', function() {
      this.style.borderColor = this.value ? 'var(--accent)' : '';
    });
  });
});

// Format large numbers
function formatNum(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toString();
}
