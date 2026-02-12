/**
 * Confetti Animation Logic
 */
export function fireConfetti() {
    const duration = 3000;
    const end = Date.now() + duration;

    (function frame() {
        // launch a few confetti from the left edge
        confetti({
            particleCount: 5,
            angle: 60,
            spread: 55,
            origin: { x: 0 }
        });
        // and launch a few from the right edge
        confetti({
            particleCount: 5,
            angle: 120,
            spread: 55,
            origin: { x: 1 }
        });

        if (Date.now() < end) {
            requestAnimationFrame(frame);
        }
    }());
}

/* 
  Ideally we should use a library like canvas-confetti. 
  The original code used `confetti` global which implies a CDN library was loaded.
  We should ensure the library is still loaded in index.html (it is via CDN).
  This wrapper just exports the fireConfetti function.
*/
