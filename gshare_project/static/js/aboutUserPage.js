function setRating(num) {
    for (let i = 1; i <= 5; i++) {
        let btn = document.getElementById('star' + i);
        if (i <= num) {
            btn.style.fontSize = '1.5rem';
        } else {
            btn.style.fontSize = '1rem';
        }
    }
    document.getElementById("rating").value = num
}