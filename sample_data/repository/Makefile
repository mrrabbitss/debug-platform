CC ?= gcc
CFLAGS ?= -Wall -Wextra -O2
all: demo

demo: main.c wifi_config.c
	$(CC) $(CFLAGS) -o $@ main.c wifi_config.c
clean:
	rm -f demo
