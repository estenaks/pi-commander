/**
 * Animate a horizontal squeeze-swap-expand on an image element.
 *
 * @param {HTMLImageElement} imgEl     - The image element to animate
 * @param {string}           newSrc   - The src to swap in at the midpoint
 * @param {function}         onSwap   - Called at scaleX(0) before expanding, use to update alt/classes/etc
 * @param {boolean}          skip     - If true, swap instantly with no animation (e.g. rotate=1 displays)
 */
function animatedCardFlip(imgEl, newSrc, onSwap, skip = false) {
    if (skip) {
        imgEl.src = newSrc;
        if (onSwap) onSwap();
        return;
    }

    const preload = new Image();
    preload.onload = () => {
        imgEl.style.animation = 'cardFlipOut 0.15s ease-in forwards';
        imgEl.addEventListener('animationend', () => {
            imgEl.style.animation = '';
            imgEl.src = newSrc;
            if (onSwap) onSwap();
            imgEl.style.animation = 'cardFlipIn 0.15s ease-out forwards';
            imgEl.addEventListener('animationend', () => {
                imgEl.style.animation = '';
            }, { once: true });
        }, { once: true });
    };
    preload.src = newSrc;
}