#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sched.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/resource.h>

/*
    The process scheduler selects an available process (ready state)
    to be executed by the CPU

    If a process with a higher priority needs to be executed, then the scheduler will block
    the current execution process and pass the cpu time to the one that has the priority

    The Operating System maintains the following important process scheduling queues:

    Job Queue -> All the process in the system

    Ready Queue -> The process that are in the main memory and are ready and waiting
    to be executed

    Device queues − The processes which are blocked due to unavailability
    of an I/O device constitute this queue.
*/

// !IMPORTANT: Execute the output as root

/*
    Apparently, if the output is not executed as root, the script can only use the kernel dynamic sched
    policy, because the other sched policies are priviliged
*/

/*
    Output:

    - The SCHED_OTHER is the default policy of the kernel, its used mainly for process that do not have a special
    priority, it will request more time when there are a lot of context switchs and the process cannot be executed continuously

    - In the Round Robin policy, all the process have an equally space time
    to be executed by the CPU, when that times reaches its end, the CPU will handle the next
    process in the queue, until that process has the opportunity to be executed again.

    - The FIFO policy is no preemptive policy, that means that when its started to be executed, it will
    continue until the process is finished or needs to perform an IO operation. He is probably the main
    reason for why the SCHED_OTHER is taking so much time.
*/

/*
    This is the parent process (this script, i know thats not right but you understood), 
    it will create child process. Each child process will create a maximum of 
    (MAX_CHILD_PROCESS) subprocess


    Monitored CPU usage time (using probably 20 child process):

    OTHER = 2860,
    RR = 2337,
    FIFO = 2163
*/

// More than twenty is probably the sufficient to crash your PC
#define MAX_CHILD_PROCESS 5

void set_scheduler(pid_t pid, int policy, int priority) {
    struct sched_param param;
    param.sched_priority = priority;

    // https://man7.org/linux/man-pages/man2/sched_setscheduler.2.html
    if (sched_setscheduler(pid, policy, &param) == -1) {
        perror("Escalonator Error");
        exit(EXIT_FAILURE);
    }
}

void cpu_bound_task(const char *name) {
    volatile long long int sum = 0;
    for (long long int i = 0; i < 1e9; i++) {
        sum += i % 10;
        if (i % 200000000 == 0) {
            printf("[%s] PID: %d RUNNING...\n", name, getpid());
        }
    }
    printf("[%s] PID: %d FINALIZED.\n", name, getpid());
}

// For each child it will create (num_processes) subprocess and ask them to perform a cpu_bound_task
void create_cpu_bound_processes(const char *name, int num_processes) {
    for (int i = 0; i < num_processes; i++) {
        pid_t pid = fork();
        if (pid == -1) {
            perror("Subprocess creation error");
            exit(1);
        }
        if (pid == 0) {
            cpu_bound_task(name);
            exit(0);
        }
    }
    while (wait(NULL) > 0);
}

void save_final_details(char *policy_name, double user_time, double system_time) {

    FILE *fp = fopen("details_file", "a");
    if (!fp)
    {
        perror("File creation failed");
        exit(EXIT_FAILURE);
    }
    fprintf(fp, "[%s] PID: %d CPU TIME: %.6f s (User: %.6f s, System: %.6f s)\n",
            policy_name, getpid(), user_time + system_time, user_time, system_time);

    fprintf(fp, "[%s] All the subprocess are FINALIZED, finishing child process. PID: %d\n\n", policy_name, getpid());
    fflush(fp);
    fclose(fp);
}

int main() {
    printf("INITIATED parent process (PID: %d)\n", getpid());

    // Even though its the last one, the FIFO still will terminate quickly than the SCHED_OTHER
    int policies[3] = {SCHED_OTHER, SCHED_RR, SCHED_FIFO};
    const char *policy_names[3] = {"SCHED_OTHER", "SCHED_RR", "SCHED_FIFO"};

    struct rusage usage_start, usage_end;

    for (int i = 0; i < 3; i++) {
        pid_t pid = fork();
        if (pid == -1) {
            perror("Child process creation error");
            exit(1);
        }

        if (pid == 0) { 
            printf("Child process CREATED: PID = %d, PPID = %d, POLICY: %s\n", getpid(), getppid(), policy_names[i]);

            int priority = (policies[i] == SCHED_OTHER) ? 0 : 10;
            set_scheduler(getpid(), policies[i], priority);

            getrusage(RUSAGE_SELF, &usage_start);

            create_cpu_bound_processes(policy_names[i], MAX_CHILD_PROCESS);

            getrusage(RUSAGE_SELF, &usage_end);

            double user_time = (usage_end.ru_utime.tv_sec - usage_start.ru_utime.tv_sec) +
                               (usage_end.ru_utime.tv_usec - usage_start.ru_utime.tv_usec) / 1e6;
            double system_time = (usage_end.ru_stime.tv_sec - usage_start.ru_stime.tv_sec) +
                                 (usage_end.ru_stime.tv_usec - usage_start.ru_stime.tv_usec) / 1e6;

            save_final_details(policy_names[i], user_time, system_time);
            exit(0);
        }
    }

    while (wait(NULL) > 0);

    printf("Parent process FINALIZED (PID: %d)\n", getpid());
    return 0;
}