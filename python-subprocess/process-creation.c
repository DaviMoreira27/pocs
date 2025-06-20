#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>

/*
Sys Calls - Low level functions that communicate directly with the kernel
Primitives - High level functions that can call one or multiple sys calls


Fork - A syscall used to create a new process in Linux and Unix Systems
 -> It will create CHILD process which will run concurrently with the parent process (the one that called the fork functions)
 -> After a new child process is created, both processes will execute the next instruction following the fork() system call.

 https://man7.org/linux/man-pages/man2/vfork.2.html
 https://pubs.opengroup.org/onlinepubs/7908799/xsh/unistd.h.html
 https://man7.org/linux/man-pages/man2/execve.2.html
*/

int main () {
    printf("Parent process initiated with the PID: %d \n", getpid());

    // Clones this process, creating an exact copy
    pid_t forkProcess1 = fork();

    if (forkProcess1 == -1)
    {
        perror("Error creating process");
        exit(1);
    }
    // Will be called 2 times
    // One for the calling process, one for the child process

    /*
        The parent process that will call the fork has a parent process itself, the getppid() will return the PID of this parent, in the end
        The parent process has a getppid() value of XXXX, because thats the PID of the shell or any other program that called the script,
        and the process created with fork will the getppid() value of the parent process (this script)

    */
    printf("Hello world! This was called by the parent and fork process, process_id(pid) = %d, in session %d, called by parent process with the id: %d \n", getpid(), geteuid(), getppid());

    pid_t forkProcess2 = fork();

    if (forkProcess2 == 0)
    {
        char *argsExecvp[] = {"ls", "-la", NULL};
        printf("Executed");
        // All the process are running in parellel, but the print ones terminated quickly
        execvp(argsExecvp[0], argsExecvp);
    } else {
        pid_t vforkProcess3 = vfork();
        if (vforkProcess3 == 0)
        {

            /*
            This function will give the control of the current process (C program) to the command.
            So, the C program is instantly replaced with the actual command.
            So, anything that comes after execvp() will NOT execute, since our program is taken over completely!
            */
            char *argsExecvp2[] = {"echo", "Executed using vfork process", NULL};
            execvp(argsExecvp2[0], argsExecvp2);
        } else {
            wait(NULL);
            printf("Parent process finalized with the PID: %d \n", getpid());
        }
    }

    return 0;
}
