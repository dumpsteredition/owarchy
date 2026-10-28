#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <wayland-client.h>
#include <stdint.h>

/* Probe the ordinary GTK/GDK API first. This submits a real empty cairo region;
 * whether GtkLayerShell's custom Wayland surface receives it is tested through
 * actual compositor pointer delivery, not inferred from this return value. */
int owarchy_try_gtk_empty_input_region(void *window_ptr, char *out, size_t out_size) {
    GdkWindow *window = GDK_WINDOW(window_ptr);
    if (!window) {
        snprintf(out, out_size, "no GdkWindow");
        return -1;
    }
    cairo_region_t *empty = cairo_region_create();
    if (!empty) {
        snprintf(out, out_size, "cairo_region_create failed");
        return -2;
    }
    int rectangles = cairo_region_num_rectangles(empty);
    gdk_window_input_shape_combine_region(window, empty, 0, 0);
    snprintf(out, out_size,
             "gdk_window_input_shape_combine_region called with empty cairo_region (%d rectangles); GTK cannot query compositor state",
             rectangles);
    cairo_region_destroy(empty);
    return rectangles == 0 ? 0 : -3;
}

/* Scoped raw Wayland fallback. wl_compositor.create_region yields an empty
 * wl_region; deliberately issue no wl_region.add. Pass that NON-NULL object to
 * wl_surface.set_input_region and commit it. Never pass NULL (which means the
 * default infinite input region). GTK owns and continues to drive this surface. */
int owarchy_set_empty_wayland_input_region(void *window_ptr, char *out, size_t out_size) {
    GdkWindow *window = GDK_WINDOW(window_ptr);
    if (!window) {
        snprintf(out, out_size, "no GdkWindow");
        return -1;
    }
    GdkDisplay *gdk_display = gdk_window_get_display(window);
    if (!gdk_display || !GDK_IS_WAYLAND_DISPLAY(gdk_display)) {
        snprintf(out, out_size, "Gdk display is not Wayland");
        return -2;
    }
    struct wl_display *display = gdk_wayland_display_get_wl_display(gdk_display);
    struct wl_compositor *compositor = gdk_wayland_display_get_wl_compositor(gdk_display);
    struct wl_surface *surface = gdk_wayland_window_get_wl_surface(window);
    if (!display || !compositor || !surface) {
        snprintf(out, out_size, "missing GTK-owned wl_display/wl_compositor/wl_surface");
        return -3;
    }
    struct wl_region *region = wl_compositor_create_region(compositor);
    if (!region) {
        snprintf(out, out_size, "wl_compositor_create_region returned NULL");
        return -4;
    }
    uint32_t surface_version = wl_proxy_get_version((struct wl_proxy *)surface);
    uint32_t region_version = wl_proxy_get_version((struct wl_proxy *)region);
    /* No wl_region_add call has occurred: region rectangle count is zero. */
    wl_surface_set_input_region(surface, region);
    wl_surface_commit(surface);
    errno = 0;
    int flushed = wl_display_flush(display);
    int saved_errno = flushed < 0 ? errno : 0;
    wl_region_destroy(region);
    if (flushed < 0 && saved_errno != EAGAIN) {
        snprintf(out, out_size,
                 "empty wl_region set+committed on GTK wl_surface but wl_display_flush failed errno=%d (%s)",
                 saved_errno, strerror(saved_errno));
        return -5;
    }
    snprintf(out, out_size,
             "created non-NULL empty wl_region (no add requests; version=%u), set on GTK wl_surface=%p (version=%u), wl_surface.commit sent, wl_display.flush=%s%s",
             region_version, (void *)surface, surface_version,
             flushed == 0 ? "success" : "EAGAIN/pending",
             flushed < 0 ? "; GTK main loop must finish flushing" : "");
    return 0;
}
