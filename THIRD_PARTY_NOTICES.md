# Third-party software and model assets

The MIT license at the repository root applies to this project's original code,
documentation, and generated demonstration artwork, not to dependencies or
downloaded models.

Python packages retain their respective upstream licenses. In particular,
Pygame is LGPL-licensed and Vosk uses the Apache 2.0 license. Consult the
licenses packaged with each installed dependency before redistributing it.

Optional expression models are downloaded from the public OpenCV Zoo project:

- YuNet face detector: MIT license.
- MobileFaceNet facial-expression model: Apache 2.0 license.
- The installer retains the relevant upstream license files beside the models
  and verifies pinned asset hashes from `deploy/expression_models.json`.

The optional small English Vosk model comes from the public
[Vosk model catalog](https://alphacephei.com/vosk/models/). The selected
`vosk-model-small-en-us-0.15` model is listed under Apache 2.0. Its download is
checksum-verified by `deploy/speech_prepare.py`.

Model binaries, user presentations, recordings, and downloaded archives are
not included in this repository. Review upstream terms before downloading or
redistributing any model. Initial package/model installation requires internet
access; normal inference uses the installed local assets.
