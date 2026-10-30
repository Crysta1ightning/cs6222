/* Controlled, persistent variant of STREAM 5.10 for CS6222.
 * Original arrays and attribution come from stream.c, retained unmodified.
 * Uses the same Copy/Scale/Add/Triad operations; this is not an official
 * STREAM submission. Buffers persist across rounds, validation is cumulative.
 */
#include <stdint.h>
#include <time.h>
#include <math.h>
#include <string.h>
#define main cs6222_original_stream_main
#include "stream.c"
#undef main

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static double expected_a = 2., expected_b = 2., expected_c = 0.;
static int iterations = 0;

static void run_kernels(double timings[4][NTIMES]) {
    for (int k = 0; k < NTIMES; k++) {
        double start = mysecond();
        #pragma omp parallel for
        for (ssize_t j=0; j<STREAM_ARRAY_SIZE; j++) c[j]=a[j];
        timings[0][k]=mysecond()-start;
        start=mysecond();
        #pragma omp parallel for
        for (ssize_t j=0; j<STREAM_ARRAY_SIZE; j++) b[j]=3.*c[j];
        timings[1][k]=mysecond()-start;
        start=mysecond();
        #pragma omp parallel for
        for (ssize_t j=0; j<STREAM_ARRAY_SIZE; j++) c[j]=a[j]+b[j];
        timings[2][k]=mysecond()-start;
        start=mysecond();
        #pragma omp parallel for
        for (ssize_t j=0; j<STREAM_ARRAY_SIZE; j++) a[j]=b[j]+3.*c[j];
        timings[3][k]=mysecond()-start;
        expected_c=expected_a;
        expected_b=3.*expected_c;
        expected_c=expected_a+expected_b;
        expected_a=expected_b+3.*expected_c;
        iterations++;
    }
}

static int validate(int full) {
    ssize_t step=full ? 1 : MAX(1, STREAM_ARRAY_SIZE/32);
    for (ssize_t j=0; j<STREAM_ARRAY_SIZE; j+=step) {
        if (!isfinite(a[j]) || !isfinite(b[j]) || !isfinite(c[j]) ||
            fabs(a[j]/expected_a-1.)>1.e-12 ||
            fabs(b[j]/expected_b-1.)>1.e-12 ||
            fabs(c[j]/expected_c-1.)>1.e-12) return 0;
    }
    return 1;
}

int main(void) {
    double timings[4][NTIMES];
    #pragma omp parallel for
    for (ssize_t j=0; j<STREAM_ARRAY_SIZE; j++) { a[j]=2.; b[j]=2.; c[j]=0.; }
    run_kernels(timings);
    if (!validate(1)) return 2;
    printf("{\"event\":\"ready\",\"array_elements\":%llu,\"iterations_per_round\":%d}\n",
           (unsigned long long)STREAM_ARRAY_SIZE, NTIMES);
    fflush(stdout);
    char command[16];
    while (fgets(command,sizeof(command),stdin)) {
        if (!strcmp(command,"QUIT\n")) {
            int valid=validate(1);
            printf("{\"event\":\"validated\",\"valid\":%s}\n",valid?"true":"false");
            fflush(stdout);
            return valid?0:2;
        }
        if (strcmp(command,"GO\n") || iterations+NTIMES>200) return 3;
        uint64_t start=now_ns();
        printf("{\"event\":\"started\",\"start_ns\":%llu}\n",(unsigned long long)start);
        fflush(stdout);
        run_kernels(timings);
        uint64_t end=now_ns();
        int valid=validate(0);
        printf("{\"event\":\"done\",\"start_ns\":%llu,\"end_ns\":%llu,\"sample_valid\":%s,\"kernels\":{",
               (unsigned long long)start,(unsigned long long)end,valid?"true":"false");
        const char *names[]={"Copy","Scale","Add","Triad"};
        for (int op=0;op<4;op++) {
            double sum=0.;
            for (int k=1;k<NTIMES;k++) sum+=timings[op][k];
            double avg=sum/(NTIMES-1);
            printf("%s\"%s\":{\"average_seconds\":%.12g,\"average_GB_per_second\":%.12g}",
                   op?",":"",names[op],avg,bytes[op]/avg/1.e9);
        }
        puts("}}");
        fflush(stdout);
        if (!valid) return 2;
    }
    return 3; /* Require QUIT and full validation, rather than silent EOF. */
}
