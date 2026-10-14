/*
 * UART console (SPEC SW-04) and SD card loading (SW-02), console.c.
 */
#ifndef CONSOLE_H
#define CONSOLE_H

/* Banner and first prompt */
void console_init(void);

/* Non-blocking: takes the characters waiting on the UART; a complete command
   line is executed, and "run" / "prbs" leave a new job block at JOB_ADDR for
   main() to run */
void console_poll(void);

/* A fresh prompt (after a job's output) */
void console_prompt(void);

/* main.c: status dump of the HPDP (SYS, link, counters, RAD), for "status" */
void hpdp_status(void);

#endif
