<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This chip performs one arithmetic operation, the one that sits underneath almost all neural network maths:

```
acc = acc + (A * B)
```

That operation is called a multiply-accumulate, or MAC. A real neural network accelerator contains hundreds or
thousands of these wired into a grid. This project is one of them, built on its own so you can watch it work.

There are three registers inside. Two of them, A and B, hold 8-bit signed operands, so each can be any whole
number from -128 to +127. The third is a 32-bit signed accumulator that holds the running total.

Negative numbers are stored in two's complement, which is the normal way hardware represents signed values. In
that scheme the top bit carries the sign, so 0000 0011 is +3 and 1111 1101 is -3.

You drive the chip one instruction at a time. The opcode goes on `uio[2:0]`, any data goes on `ui[7:0]`, and the
instruction takes effect on the next rising clock edge.

| Opcode | Name   | What it does                          |
|--------|--------|---------------------------------------|
| 000    | NOP    | Nothing changes                       |
| 001    | LOAD_A | Copy `ui[7:0]` into register A        |
| 010    | LOAD_B | Copy `ui[7:0]` into register B        |
| 011    | MAC    | Add A times B to the accumulator      |
| 100    | CLEAR  | Set the accumulator back to zero      |
| 101, 110, 111 | (unused) | Treated the same as NOP        |

The accumulator is 32 bits wide but there are only 8 output pins, so the result comes out one byte at a time.
Two more input bits, `uio[4:3]`, pick which byte appears on `uo[7:0]`.

| `uio[4:3]` | Byte shown on `uo[7:0]` |
|------------|-------------------------|
| 00         | Bits 7 down to 0        |
| 01         | Bits 15 down to 8       |
| 10         | Bits 23 down to 16      |
| 11         | Bits 31 down to 24      |

That byte selector is pure combinational logic. Changing the two select bits changes the output straight away,
with no clock edge needed, so you can read all four bytes just by walking the switches through the four settings.

Pulling `rst_n` low clears A, B and the accumulator to zero on the next clock edge.

One detail worth knowing, because it is the part most likely to be asked about. The product of two 8-bit signed
numbers is held in 16 bits. Fifteen bits would be enough for every product except one. The exception is
-128 * -128, which is +16384, exactly 2 to the power 14. Squeeze that into 15 bits and the value lands on the
sign bit and reads back as -16384 instead. No tool warns you. The design uses the full 16 bits for that reason,
and there is a test that fails if anyone ever narrows it.

## How to test

The quickest check is the simulation. With Icarus Verilog and cocotb installed:

```
cd test
make
```

That runs twelve tests covering positive and negative operands, accumulation across several MACs, the byte
selector, reset, and the unused opcodes. All twelve should pass.

On the demo board, three controls matter. The data bus `ui[7:0]` comes from the bank of input DIP switches. The
opcode and byte select on `uio[4:0]` are driven by the on-board RP2040, which you set from the Commander app.
The clock can be single stepped from the Commander app's INTERACT tab, which is what makes it possible to walk
through a calculation one instruction at a time and watch what happens. The answer appears on the 7-segment
display.

Every instruction follows the same rhythm: set up the inputs, then advance the clock one step.

To work out 3 * 4 = 12:

1. Pull `rst_n` low, then release it, to start from a clean state.
2. Set the opcode to `100` (CLEAR) and pulse the clock.
3. Set `ui[7:0]` to 3 (`0000 0011`), set the opcode to `001` (LOAD_A), and pulse the clock.
4. Set `ui[7:0]` to 4 (`0000 0100`), set the opcode to `010` (LOAD_B), and pulse the clock.
5. Set the opcode to `011` (MAC) and pulse the clock.
6. Set the opcode back to `000` (NOP) and set `uio[4:3]` to `00`. The output now reads 12 (`0000 1100`).

Leave the opcode at NOP and change `uio[4:3]` to `01`, `10` and `11` to see the upper three bytes. For a small
positive answer like 12 they are all zero.

To see a negative result, repeat the sequence with `ui[7:0]` set to `1111 1101` for A, which is -3. The answer
is -12, and the four bytes read `F4`, `FF`, `FF`, `FF` from lowest to highest, which is -12 in 32-bit two's
complement.

To see accumulation, skip the CLEAR and just load new operands and run MAC again. The products keep adding up.

## External hardware

None. Everything the design needs is already on the demo board: the input DIP switches for the data bus, the
RP2040 for the opcode and byte-select pins and for stepping the clock, and the 7-segment display for the result.
