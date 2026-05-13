function clearDisplay() {
    document.getElementById('display').value = '';
}

function deleteLastChar() {
    let display = document.getElementById('display');
    display.value = display.value.slice(0, -1);
}

function appendCharacter(character) {
    document.getElementById('display').value += character;
}

function calculateResult() {
    try {
        let display = document.getElementById('display');
        display.value = eval(display.value);
    } catch (error) {
        display.value = 'Error';
    }
}