"""hota-gallery: Art-Net lighting control for the HOTA Gallery facade.

Replaces the discontinued ELM (ENTTEC) system that ran this building before.
Pure-stdlib backend (same reasoning as sacn2wiz: one less moving part on a
Pi), with Pillow/ffmpeg reached for only in the media-processing step - see
the project plan for why that split exists.
"""
