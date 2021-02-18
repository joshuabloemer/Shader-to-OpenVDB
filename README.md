# Shader to OpenVDB (.vdb)

This addon is designed to export volumetric materials from Blender’s node editor to the OpenVDB file format.

## Usage

1. Select your object with the material you want to export in the first material slot.
2. Click File > Export > OpenVDB (.vdb)
3. Configure options 
4. Profit

## Options

Voxel Count
	Number of voxels in each dimension
Clamp Negative
	Clamps negative density values to 0

## List of supported Nodes

* Texture Coordinate (Generated and Object)
* RGB
* Value
* Reroute
* Math
* Vector Math
* Separate XYZ
* Separate RGB
* Separate HSV
* Combine XYZ
* Combine RGB
* Combine HSV
* Clamp
* Map Range
* Color Ramp

## Notes

It is recommended to open the system console before starting the export to view a progress bar
(Window > Toggle System Console)

Although the Addon works on 2.80+ it is only fully tested and supported in 2.90

Broken since 2.91
https://devtalk.blender.org/t/issue-while-importing-pyd-module/16570

## Links

### Social

https://twitter.com/SirJyoshi

### Discord

https://discord.gg/8NqjJAhFFc

### Github

https://github.com/joshuabloemer/Shader-to-OpenVDB