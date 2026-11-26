"""The dark "galaxy" look. Plugin accents are generated from each manifest's color, so a new plugin
gets its own glow without touching this file."""
from __future__ import annotations

from superapp_core import PluginManifest

BASE_CSS = """
window.superapp { background-color: #05060a;
  background-image: radial-gradient(circle, alpha(white, 0.35) 0.7px, transparent 1.4px),
                    radial-gradient(circle, alpha(#7dd3fc, 0.25) 0.7px, transparent 1.4px),
                    radial-gradient(ellipse at 10% 0%, alpha(#1e3a8a, 0.16), transparent 50%),
                    radial-gradient(ellipse at 100% 100%, alpha(#0e7490, 0.10), transparent 50%);
  background-size: 137px 149px, 211px 197px, 100% 100%, 100% 100%;
  background-position: 0 0, 60px 90px, 0 0, 0 0;
  background-repeat: repeat, repeat, no-repeat, no-repeat; }
window.superapp headerbar { background: transparent; box-shadow: none; }
.hero { padding: 22px 24px; border-radius: 22px; color: white; background-color: #0d1017;
        background-image: radial-gradient(ellipse at 100% 0%, alpha(#22d3ee, 0.16), transparent 60%),
                          radial-gradient(ellipse at 0% 100%, alpha(#3b82f6, 0.10), transparent 60%);
        box-shadow: inset 0 0 0 1px alpha(#22d3ee, 0.22); }
.hero-title { font-size: 1.9em; font-weight: 800; }
.hero-sub { color: alpha(white, 0.6); }
.glow { border-radius: 22px; padding: 18px; background-color: alpha(#0d1017, 0.95);
        box-shadow: inset 0 0 0 1px alpha(white, 0.10); }
.head { padding: 2px; border-radius: 14px; }
.card-title { font-size: 1.15em; font-weight: 800; color: white; }
.card-sub { font-size: 0.85em; color: alpha(white, 0.5); }
.stat { font-size: 2.6em; font-weight: 800; color: white; }
.stat-label { color: alpha(white, 0.5); }
.dot { min-width: 8px; min-height: 8px; border-radius: 999px; background: alpha(white, 0.18); }
.dot.on { background: #22d3ee; box-shadow: 0 0 8px #22d3ee; }
.batt trough { min-height: 6px; border-radius: 999px; background: alpha(white, 0.06); }
.batt progress { min-height: 6px; border-radius: 999px; background: linear-gradient(90deg, #22d3ee, #3b82f6); }
.batt.low progress { background: linear-gradient(90deg, #ffbe6f, #e66100); }
.ring-label { font-weight: 800; color: white; }
.pill { border-radius: 999px; padding: 6px 16px; font-weight: bold; background: alpha(white, 0.04);
        color: alpha(white, 0.85); box-shadow: inset 0 0 0 1px alpha(white, 0.10); }
.pill:hover { background: alpha(white, 0.08); }
.quick-title { font-weight: 800; color: alpha(white, 0.6); }
.quick { border-radius: 999px; padding: 6px 14px; background: alpha(#0d1017, 0.9); color: alpha(white, 0.85);
         box-shadow: inset 0 0 0 1px alpha(white, 0.08); }
.quick:hover { box-shadow: inset 0 0 0 1px alpha(#22d3ee, 0.5); }
.empty { padding: 28px; border-radius: 22px; background: alpha(#0d1017, 0.9); color: alpha(white, 0.7); }
"""


def css_class(manifest: PluginManifest) -> str:
    return "p-" + manifest.id


def accent_css(manifest: PluginManifest) -> str:
    c, k = manifest.color, css_class(manifest)
    return f"""
.glow.{k} {{ background-image: radial-gradient(circle at 95% 0%, alpha({c}, 0.12), transparent 55%);
             box-shadow: inset 0 0 0 1px alpha({c}, 0.22); }}
button.pill.lead-{k} {{ background: alpha({c}, 0.12); color: {c}; box-shadow: inset 0 0 0 1px alpha({c}, 0.55); }}
button.pill.lead-{k}:hover {{ background: alpha({c}, 0.20);
                             box-shadow: inset 0 0 0 1px {c}, 0 0 14px alpha({c}, 0.25); }}
"""


def build_css(manifests: list[PluginManifest]) -> str:
    return BASE_CSS + "".join(accent_css(m) for m in manifests)
