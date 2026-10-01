"""What a stock COCO-pretrained detector can and can't stand in for, from the spec's object list
(section 6). Kept as its own module because this mapping is the one thing in Phase 2 that a
person should be able to check at a glance without reading detector code.

Only "person", "scissors" and COCO's "dining table" (-> table) genuinely resemble what they're
standing in for. Nothing in the standard 80 COCO classes looks like flat/folded fabric, a piece
of chalk or a tailor's marker, a measuring scale, a paper pattern, or a sewing machine - so this
detector does not produce those classes at all, rather than mapping them to something that
merely happens to be COCO's closest *keyword* match (which would just be a confident-looking
wrong answer). Real garment classes need training data from the actual factory floor (spec
section 7) - see PLAN.md, Phase 2.
"""
from __future__ import annotations

# COCO class name -> the spec's vocabulary name for it.
COCO_TO_GARMENT: dict[str, str] = {
    "person": "person",
    "scissors": "scissors",
    "dining table": "table",
}

# The spec's required classes (section 6) with no usable COCO stand-in - see the module
# docstring for why. Detection() objects from this module never use these names.
NOT_YET_DETECTABLE: tuple[str, ...] = (
    "fabric", "marking_tool", "measuring_scale", "pattern", "sewing_machine",
    "fabric_piece", "bundle",
)
