/** Sample JavaScript module for code_index tests. */

class Calculator {
  /** Add two numbers. */
  add(a, b) {
    return a + b;
  }
}

function compute(x, y) {
  const calc = new Calculator();
  return calc.add(x, y);
}
