CC ?= cc
PKG_CONFIG ?= pkg-config
PKGS := gtk+-3.0 gdk-wayland-3.0 wayland-client
TARGET := engine/libowarchy-input-region.so
SOURCE := engine/owarchy-input-region.c
GTK_CFLAGS = $(shell $(PKG_CONFIG) --cflags $(PKGS))
GTK_LIBS = $(shell $(PKG_CONFIG) --libs $(PKGS))
CFLAGS ?= -O2

.PHONY: all helper check test clean

all: helper

helper: $(TARGET)

$(TARGET): $(SOURCE)
	@$(PKG_CONFIG) --exists $(PKGS) || { printf '%s\n' 'Missing build dependencies: C compiler, pkg-config/pkgconf, GTK 3/GDK Wayland, and Wayland client development files.' >&2; exit 1; }
	$(CC) $(CPPFLAGS) $(GTK_CFLAGS) $(CFLAGS) -std=c11 -Wall -Wextra -fPIC -shared -o "$@" "$<" $(LDFLAGS) $(GTK_LIBS)

check: helper
	python3 -m py_compile control_state.py engine/manager.py engine/vte_layer.py tests/test_package.py
	python3 -m unittest discover -s tests -v

test: check

clean:
	rm -f $(TARGET)
	rm -rf __pycache__ engine/__pycache__ tests/__pycache__
