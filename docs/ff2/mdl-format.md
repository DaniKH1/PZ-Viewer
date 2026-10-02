# FF2 PS2 character MDL

FF2 `.mdl` character packages have their own reader in
`pz_core/ff2/pz_mdl_ff2.py`; they do not call or inherit from the FF1 MDL parser.
The package walk follows the character flow in Obscura's
`Model::ExtractModel`, `ReadTextures`, and `ReadSGD`:

1. The outer PK2 entry 0 is the nested model pack. Each nested entry is an
   FF2 0x1050 SGD.
2. The outer PK2 entry 1 is the nested TIM2 texture pack.
3. Later SGDs without a coordinate table use the top SGD's skeleton.

Geometry is parsed by `pz_sgd_ff2`, including FF2 vertex-colour recovery and
full TEX0 metadata. Textures are decoded by `pz_tim2_ff2` and linked to
materials by TBP0. The FF2 browser selection dispatches `.mdl` files to this
module; other game categories keep their own readers.

Obscura source reference:
<https://github.com/Mikompilation/Obscura/blob/f7e041f5389a4ef8009d186b7222bace9caee503/ModelConverter/game/Model.cpp>
