// main.js — RugScan global JS utilities

// ========================================
// MOBILE MENU TOGGLE
// ========================================
document.addEventListener('DOMContentLoaded', () => {
  const menuToggle = document.getElementById('mobileMenuToggle');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('mobileOverlay');

  // Toggle mobile menu
  function toggleMobileMenu() {
    sidebar.classList.toggle('mobile-active');
    overlay.classList.toggle('active');
    
    // Update button icon
    if (sidebar.classList.contains('mobile-active')) {
      menuToggle.innerHTML = '✕';
    } else {
      menuToggle.innerHTML = '☰';
    }
  }

  // Close mobile menu
  function closeMobileMenu() {
    sidebar.classList.remove('mobile-active');
    overlay.classList.remove('active');
    menuToggle.innerHTML = '☰';
  }

  // Event listeners
  if (menuToggle) {
    menuToggle.addEventListener('click', toggleMobileMenu);
  }

  if (overlay) {
    overlay.addEventListener('click', closeMobileMenu);
  }

  // Close menu when clicking nav link
  document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 900) {
        closeMobileMenu();
      }
    });
  });

  // Close menu on window resize if viewport becomes larger
  window.addEventListener('resize', () => {
    if (window.innerWidth > 900) {
      closeMobileMenu();
    }
  });

  // Prevent body scroll when menu is open
  const observer = new MutationObserver(() => {
    if (sidebar.classList.contains('mobile-active')) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
  });

  observer.observe(sidebar, {
    attributes: true,
    attributeFilter: ['class']
  });

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

// ========================================
// TABLE SCROLL INDICATOR
// ========================================
document.addEventListener('DOMContentLoaded', () => {
  const tableWraps = document.querySelectorAll('.table-wrap');
  
  tableWraps.forEach(wrap => {
    wrap.addEventListener('scroll', function() {
      if (this.scrollLeft > 10) {
        this.classList.add('scrolled');
      } else {
        this.classList.remove('scrolled');
      }
    });
  });
});
