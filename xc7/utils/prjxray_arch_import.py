""" Generates the top level VPR arch XML from the Project X-Ray database.

By default this will generate a complete arch XML for all tile types specified.

If the --use_roi flag is passed, only the tiles within the ROI will be included,
and synthetic IO pads will be created and connected to the routing fabric.
The mapping of the pad name to synthetic tile location will be outputted to the
file specified in the --synth_tiles output argument.  This can be used to generate
IO placement spefications to target the synthetic IO pads.

"""
from __future__ import print_function
import argparse
import prjxray.db
from prjxray.roi import Roi
from prjxray import grid_types
import os.path
import simplejson as json
import sys

import lxml.etree as ET

from prjxray_db_cache import DatabaseCache
from prjxray_tile_import import add_vpr_tile_prefix


def create_synth_io_tiles(complexblocklist_xml, tiles_xml, pb_name, is_input):
    """ Creates synthetic IO pad tiles used to connect ROI inputs and outputs to the routing network.
    """
    pb_xml = ET.SubElement(
        complexblocklist_xml, 'pb_type', {
            'name': pb_name,
        }
    )

    tile_xml = ET.SubElement(tiles_xml, 'tile', {
        'name': pb_name,
    })

    equivalent_sites = ET.SubElement(tile_xml, 'equivalent_sites')
    site = ET.SubElement(equivalent_sites, 'site', {'pb_type': pb_name})

    ET.SubElement(
        tile_xml, 'fc', {
            'in_type': 'abs',
            'in_val': '2',
            'out_type': 'abs',
            'out_val': '2',
        }
    )

    interconnect_xml = ET.SubElement(pb_xml, 'interconnect')

    if is_input:
        blif_model = '.input'
        pad_name = 'inpad'
        port_type = 'output'
    else:
        blif_model = '.output'
        pad_name = 'outpad'
        port_type = 'input'

    ET.SubElement(pb_xml, port_type, {
        'name': pad_name,
        'num_pins': '1',
    })

    ET.SubElement(tile_xml, port_type, {
        'name': pad_name,
        'num_pins': '1',
    })

    port_pin = '{}.{}'.format(pb_name, pad_name)
    pad_pin = '{}.{}'.format(pad_name, pad_name)

    ET.SubElement(site, 'direct', {'from': port_pin, 'to': port_pin})

    if not is_input:
        input_name = port_pin
        output_name = pad_pin
    else:
        input_name = pad_pin
        output_name = port_pin

    pin_pb_type = ET.SubElement(
        pb_xml, 'pb_type', {
            'name': pad_name,
            'blif_model': blif_model,
            'num_pb': '1',
        }
    )
    ET.SubElement(
        pin_pb_type, port_type, {
            'name': pad_name,
            'num_pins': '1',
        }
    )

    direct_xml = ET.SubElement(
        interconnect_xml, 'direct', {
            'name': '{}_to_{}'.format(input_name, output_name),
            'input': input_name,
            'output': output_name,
        }
    )

    ET.SubElement(
        direct_xml, 'delay_constant', {
            'max': '1e-11',
            'in_port': input_name,
            'out_port': output_name,
        }
    )


def create_synth_constant_tiles(
        model_xml, complexblocklist_xml, tiles_xml, pb_name, signal
):
    """ Creates synthetic constant tile generates some constant signal.

    Routing import will create a global network to fan this signal to local
    constant sources.
    """
    pb_xml = ET.SubElement(
        complexblocklist_xml, 'pb_type', {
            'name': pb_name,
        }
    )

    tile_xml = ET.SubElement(tiles_xml, 'tile', {
        'name': pb_name,
    })

    equivalent_sites = ET.SubElement(tile_xml, 'equivalent_sites')
    site = ET.SubElement(equivalent_sites, 'site', {'pb_type': pb_name})

    ET.SubElement(
        tile_xml, 'fc', {
            'in_type': 'abs',
            'in_val': '2',
            'out_type': 'abs',
            'out_val': '2',
        }
    )

    interconnect_xml = ET.SubElement(pb_xml, 'interconnect')

    blif_model = '.subckt ' + signal
    port_type = 'output'
    pin_name = signal

    ET.SubElement(pb_xml, port_type, {
        'name': pin_name,
        'num_pins': '1',
    })

    ET.SubElement(tile_xml, port_type, {
        'name': pin_name,
        'num_pins': '1',
    })

    port_pin = '{}.{}'.format(pb_name, pin_name)
    pad_pin = '{}.{}'.format(pin_name, pin_name)

    ET.SubElement(site, 'direct', {'from': port_pin, 'to': port_pin})

    input_name = pad_pin
    output_name = port_pin

    pin_pb_type = ET.SubElement(
        pb_xml, 'pb_type', {
            'name': pin_name,
            'blif_model': blif_model,
            'num_pb': '1',
        }
    )
    ET.SubElement(
        pin_pb_type, port_type, {
            'name': pin_name,
            'num_pins': '1',
        }
    )

    direct_xml = ET.SubElement(
        interconnect_xml, 'direct', {
            'name': '{}_to_{}'.format(input_name, output_name),
            'input': input_name,
            'output': output_name,
        }
    )

    ET.SubElement(
        direct_xml, 'delay_constant', {
            'max': '1e-11',
            'in_port': input_name,
            'out_port': output_name,
        }
    )

    model = ET.SubElement(model_xml, 'model', {
        'name': signal,
    })

    ET.SubElement(model, 'input_ports')
    output_ports = ET.SubElement(model, 'output_ports')
    ET.SubElement(output_ports, 'port', {
        'name': pin_name,
    })


def get_phy_tiles(conn, tile_pkey):
    """ Returns the locations of all physical tiles for specified tile. """
    c = conn.cursor()
    c2 = conn.cursor()

    phy_locs = []
    for (phy_tile_pkey, ) in c.execute(
            "SELECT phy_tile_pkey FROM tile_map WHERE tile_pkey = ?",
        (tile_pkey, )):
        c2.execute(
            "SELECT grid_x, grid_y FROM phy_tile WHERE pkey = ?",
            (phy_tile_pkey, )
        )
        loc = c2.fetchone()
        phy_locs.append(grid_types.GridLoc(*loc))

    return phy_locs


def is_in_roi(conn, roi, tile_pkey):
    """ Returns if the specified tile is in the ROI. """
    phy_locs = get_phy_tiles(conn, tile_pkey)
    return any(roi.tile_in_roi(loc) for loc in phy_locs)


def get_fasm_tile_prefix(conn, g, tile_pkey, site_as_tile_pkey):
    """ Returns FASM prefix of specified tile. """
    c = conn.cursor()

    c.execute(
        """
SELECT
    phy_tile.name,
    tile_type.name
FROM phy_tile
INNER JOIN tile_type
ON phy_tile.tile_type_pkey = tile_type.pkey
WHERE
    phy_tile.pkey IN (SELECT phy_tile_pkey FROM tile_map WHERE tile_pkey = ?);
        """, (tile_pkey, )
    )

    # If this tile has multiples phy_tile's, make sure only one has bitstream
    # data, otherwise the tile split was invalid.
    tile_type_map = {}
    for tilename, tile_type in c:
        gridinfo = g.gridinfo_at_tilename(tilename)
        is_vbrk = gridinfo.tile_type.find('VBRK') != -1

        # VBRK tiles are known to have no bitstream data.
        if not is_vbrk and not gridinfo.bits:
            print(
                '*** WARNING *** Tile {} appears to be missing bitstream data.'
                .format(tilename),
                file=sys.stderr
            )

        if gridinfo.bits:
            # Each VPR tile can only have one prefix.
            # If this assumption is violated, a more dramatic
            # restructing is required.
            tile_type_map[tile_type] = tilename

    if len(tile_type_map) > 1:
        return lambda single_xml: attach_multiple_prefixes_to_tile(
            single_xml, tile_type_map
        )
    else:
        assert len(tile_type_map) == 1, tile_pkey

        tile_prefix = list(tile_type_map.values())[0]

        # If this tile is site_as_tile, add an additional prefix of the site
        # that is embedded in the tile.
        if site_as_tile_pkey is not None:
            c.execute(
                "SELECT site_pkey FROM site_as_tile WHERE pkey = ?",
                (site_as_tile_pkey, )
            )
            site_pkey = c.fetchone()[0]

            c.execute(
                """
                SELECT site_type_pkey, x_coord FROM site WHERE pkey = ?
                """, (site_pkey, )
            )
            site_type_pkey, x = c.fetchone()

            c.execute(
                "SELECT name FROM site_type WHERE pkey = ?",
                (site_type_pkey, )
            )
            site_type_name = c.fetchone()[0]

            tile_prefix = '{}.{}_X{}'.format(tile_prefix, site_type_name, x)

        return lambda single_xml: attach_prefix_to_tile(
            single_xml, tile_prefix
        )


def attach_prefix_to_tile(single_xml, fasm_tile_prefix):
    meta = ET.SubElement(single_xml, 'metadata')
    ET.SubElement(
        meta, 'meta', {
            'name': 'fasm_prefix',
        }
    ).text = fasm_tile_prefix


# Map the following tile types to a more general name
TYPE_REMAP = {
    "LIOI3_TBYTESRC": "LIOI3",
    "LIOI3_TBYTETERM": "LIOI3",
    "RIOI3_TBYTESRC": "RIOI3",
    "RIOI3_TBYTETERM": "RIOI3",
}


def attach_multiple_prefixes_to_tile(single_xml, tile_type_map):
    meta = ET.SubElement(single_xml, 'metadata')
    ET.SubElement(meta, 'meta', {
        'name': 'fasm_placeholders',
    }).text = '\n' + '\n'.join(
        '{} : {}'.format(TYPE_REMAP.get(k, k), v)
        for k, v in tile_type_map.items()
    ) + '\n'


def get_tiles(conn, g, roi, synth_loc_map, synth_tile_map, tile_types):
    """ Yields tiles in grid.

    Yields
    ------
    vpr_tile_type : str
        VPR tile type at this grid location.
    grid_x, grid_y : int
        Grid coordinate of tile
    metadata_function : function that takes lxml.Element
        Function for attaching metadata tags to <single> elements.
        Function must be supplied, but doesn't need to add metadata if not
        required.

    """
    c = conn.cursor()
    c2 = conn.cursor()

    only_emit_roi = roi is not None

    for tile_pkey, grid_x, grid_y, phy_tile_pkey, tile_type_pkey, site_as_tile_pkey in c.execute(
            """
        SELECT pkey, grid_x, grid_y, phy_tile_pkey, tile_type_pkey, site_as_tile_pkey FROM tile
        """):

        # Just output synth tiles, no additional processing is required here.
        if (grid_x, grid_y) in synth_loc_map:
            vpr_tile_type = synth_loc_map[(grid_x, grid_y)]

            yield vpr_tile_type, grid_x, grid_y, lambda x: None
            continue

        c2.execute(
            "SELECT name FROM tile_type WHERE pkey = ?", (tile_type_pkey, )
        )
        tile_type = c2.fetchone()[0]
        if tile_type not in tile_types:
            # We don't want this tile
            continue

        if only_emit_roi and not is_in_roi(conn, roi, tile_pkey):
            # Tile is outside ROI, skip it
            continue

        vpr_tile_type = add_vpr_tile_prefix(tile_type)

        meta_fun = get_fasm_tile_prefix(conn, g, tile_pkey, site_as_tile_pkey)

        yield vpr_tile_type, grid_x, grid_y, meta_fun


def add_synthetic_tiles(model_xml, complexblocklist_xml, tiles_xml, need_io):
    synth_tile_types = {}
    if need_io:
        create_synth_io_tiles(
            complexblocklist_xml, tiles_xml, 'SYN-INPAD', is_input=True
        )
        create_synth_io_tiles(
            complexblocklist_xml, tiles_xml, 'SYN-OUTPAD', is_input=False
        )
        synth_tile_types['output'] = 'SYN-INPAD'
        synth_tile_types['input'] = 'SYN-OUTPAD'

    create_synth_constant_tiles(
        model_xml, complexblocklist_xml, tiles_xml, 'SYN-VCC', 'VCC'
    )
    create_synth_constant_tiles(
        model_xml, complexblocklist_xml, tiles_xml, 'SYN-GND', 'GND'
    )

    synth_tile_types['VCC'] = 'SYN-VCC'
    synth_tile_types['GND'] = 'SYN-GND'

    return synth_tile_types


def insert_constant_tiles(conn, model_xml, complexblocklist_xml, tiles_xml):
    c = conn.cursor()

    # Always add 'GND' and 'VCC' synth tiles
    synth_tile_map = add_synthetic_tiles(
        model_xml, complexblocklist_xml, tiles_xml, need_io=False
    )
    synth_loc_map = {}

    c.execute('SELECT pkey FROM tile_type WHERE name = "NULL";')
    null_tile_type_pkey = c.fetchone()[0]

    c.execute(
        """
    SELECT pkey, tile_type_pkey FROM phy_tile
    WHERE grid_x = 1 AND grid_y = 0"""
    )
    vcc_phy_tile_pkey, vcc_tile_type_pkey = c.fetchone()
    assert vcc_tile_type_pkey == null_tile_type_pkey, vcc_tile_type_pkey

    c.execute(
        """
    SELECT pkey, grid_x, grid_y FROM tile WHERE phy_tile_pkey = ?
    """, (vcc_phy_tile_pkey, )
    )
    results = c.fetchall()
    assert len(results) == 1, results
    _, vcc_grid_x, vcc_grid_y = results[0]
    synth_loc_map[(vcc_grid_x, vcc_grid_y)] = synth_tile_map['VCC']

    c.execute(
        """
    SELECT pkey, tile_type_pkey FROM phy_tile
    WHERE grid_x = 2 AND grid_y = 0"""
    )
    gnd_phy_tile_pkey, gnd_tile_type_pkey = c.fetchone()
    assert gnd_tile_type_pkey == null_tile_type_pkey

    c.execute(
        """
    SELECT pkey, grid_x, grid_y FROM tile WHERE phy_tile_pkey = ?
    """, (gnd_phy_tile_pkey, )
    )
    results = c.fetchall()
    assert len(results) == 1, results
    _, gnd_grid_x, gnd_grid_y = results[0]
    synth_loc_map[(gnd_grid_x, gnd_grid_y)] = synth_tile_map['GND']

    return synth_tile_map, synth_loc_map


def main():
    mydir = os.path.dirname(__file__)
    prjxray_db = os.path.abspath(
        os.path.join(mydir, "..", "..", "third_party", "prjxray-db")
    )

    db_types = prjxray.db.get_available_databases(prjxray_db)

    parser = argparse.ArgumentParser(description="Generate arch.xml")
    parser.add_argument(
        '--part',
        choices=[os.path.basename(db_type) for db_type in db_types],
        help="""Project X-Ray database to use."""
    )
    parser.add_argument(
        '--output-arch',
        nargs='?',
        type=argparse.FileType('w'),
        help="""File to output arch."""
    )
    parser.add_argument(
        '--tile-types', help="Semi-colon seperated tile types."
    )
    parser.add_argument(
        '--pin_assignments', required=True, type=argparse.FileType('r')
    )
    parser.add_argument('--use_roi', required=False)
    parser.add_argument('--device', required=True)
    parser.add_argument('--synth_tiles', required=False)
    parser.add_argument('--connection_database', required=True)
    parser.add_argument(
        '--graph_limit',
        help='Limit grid to specified dimensions in x_min,y_min,x_max,y_max',
    )

    args = parser.parse_args()

    tile_types = args.tile_types.split(',')

    tile_model = "../../tiles/{0}/{0}.model.xml"
    tile_pbtype = "../../tiles/{0}/{0}.pb_type.xml"
    tile_tile = "../../tiles/{0}/{0}.tile.xml"

    xi_url = "http://www.w3.org/2001/XInclude"
    ET.register_namespace('xi', xi_url)
    xi_include = "{%s}include" % xi_url

    arch_xml = ET.Element(
        'architecture',
        {},
        nsmap={'xi': xi_url},
    )

    model_xml = ET.SubElement(arch_xml, 'models')
    for tile_type in tile_types:
        ET.SubElement(
            model_xml, xi_include, {
                'href': tile_model.format(tile_type.lower()),
                'xpointer': "xpointer(models/child::node())",
            }
        )

    tiles_xml = ET.SubElement(arch_xml, 'tiles')
    for tile_type in tile_types:
        ET.SubElement(
            tiles_xml, xi_include, {
                'href': tile_tile.format(tile_type.lower()),
            }
        )

    complexblocklist_xml = ET.SubElement(arch_xml, 'complexblocklist')
    for tile_type in tile_types:
        ET.SubElement(
            complexblocklist_xml, xi_include, {
                'href': tile_pbtype.format(tile_type.lower()),
            }
        )

    layout_xml = ET.SubElement(arch_xml, 'layout')
    db = prjxray.db.Database(os.path.join(prjxray_db, args.part))
    g = db.grid()

    synth_tiles = {}
    synth_tiles['tiles'] = {}
    synth_loc_map = {}
    synth_tile_map = {}
    roi = None
    if args.use_roi:
        with open(args.use_roi) as f:
            j = json.load(f)

        with open(args.synth_tiles) as f:
            synth_tiles = json.load(f)

        roi = Roi(
            db=db,
            x1=j['info']['GRID_X_MIN'],
            y1=j['info']['GRID_Y_MIN'],
            x2=j['info']['GRID_X_MAX'],
            y2=j['info']['GRID_Y_MAX'],
        )

        synth_tile_map = add_synthetic_tiles(
            model_xml, complexblocklist_xml, tiles_xml, need_io=True
        )

        for _, tile_info in synth_tiles['tiles'].items():
            assert tuple(tile_info['loc']) not in synth_loc_map

            assert len(tile_info['pins']) == 1

            vpr_tile_type = synth_tile_map[tile_info['pins'][0]['port_type']]

            synth_loc_map[tuple(tile_info['loc'])] = vpr_tile_type

    elif args.graph_limit:
        x_min, y_min, x_max, y_max = map(int, args.graph_limit.split(','))
        roi = Roi(
            db=db,
            x1=x_min,
            y1=y_min,
            x2=x_max,
            y2=y_max,
        )

    with DatabaseCache(args.connection_database, read_only=True) as conn:
        c = conn.cursor()

        if 'GND' not in synth_tile_map:
            synth_tile_map, synth_loc_map = insert_constant_tiles(
                conn, model_xml, complexblocklist_xml, tiles_xml
            )

        # Find the grid extent.
        y_max = 0
        x_max = 0
        for grid_x, grid_y in c.execute("SELECT grid_x, grid_y FROM tile"):
            x_max = max(grid_x + 2, x_max)
            y_max = max(grid_y + 2, y_max)

        name = '{}-test'.format(args.device)
        fixed_layout_xml = ET.SubElement(
            layout_xml, 'fixed_layout', {
                'name': name,
                'height': str(y_max),
                'width': str(x_max),
            }
        )

        for vpr_tile_type, grid_x, grid_y, metadata_function in get_tiles(
                conn=conn,
                g=g,
                roi=roi,
                synth_loc_map=synth_loc_map,
                synth_tile_map=synth_tile_map,
                tile_types=tile_types,
        ):
            single_xml = ET.SubElement(
                fixed_layout_xml, 'single', {
                    'priority': '1',
                    'type': vpr_tile_type,
                    'x': str(grid_x),
                    'y': str(grid_y),
                }
            )
            metadata_function(single_xml)

        switchlist_xml = ET.SubElement(arch_xml, 'switchlist')

        for name, internal_capacitance, drive_resistance, intrinsic_delay, \
                switch_type in c.execute("""
SELECT
    name,
    internal_capacitance,
    drive_resistance,
    intrinsic_delay,
    switch_type
FROM
    switch;"""):
            attrib = {
                'type': switch_type,
                'name': name,
                "R": str(drive_resistance),
                "Cin": str(0),
                "Cout": str(0),
                "Tdel": str(intrinsic_delay),
            }

            if internal_capacitance != 0:
                attrib["Cinternal"] = str(internal_capacitance)

            if False:
                attrib["mux_trans_size"] = str(0)
                attrib["buf_size"] = str(0)

            ET.SubElement(switchlist_xml, 'switch', attrib)

        segmentlist_xml = ET.SubElement(arch_xml, 'segmentlist')

        # VPR requires a segment, so add one.
        dummy_xml = ET.SubElement(
            segmentlist_xml, 'segment', {
                'name': 'dummy',
                'length': '2',
                'freq': '1.0',
                'type': 'bidir',
                'Rmetal': '0',
                'Cmetal': '0',
            }
        )
        ET.SubElement(dummy_xml, 'wire_switch', {
            'name': 'buffer',
        })
        ET.SubElement(dummy_xml, 'opin_switch', {
            'name': 'buffer',
        })
        ET.SubElement(dummy_xml, 'sb', {
            'type': 'pattern',
        }).text = ' '.join('1' for _ in range(3))
        ET.SubElement(dummy_xml, 'cb', {
            'type': 'pattern',
        }).text = ' '.join('1' for _ in range(2))

        for (name, length) in c.execute("SELECT name, length FROM segment"):
            if length is None:
                length = 1

            segment_xml = ET.SubElement(
                segmentlist_xml, 'segment', {
                    'name': name,
                    'length': str(length),
                    'freq': '1.0',
                    'type': 'bidir',
                    'Rmetal': '0',
                    'Cmetal': '0',
                }
            )
            ET.SubElement(segment_xml, 'wire_switch', {
                'name': 'buffer',
            })
            ET.SubElement(segment_xml, 'opin_switch', {
                'name': 'buffer',
            })
            ET.SubElement(segment_xml, 'sb', {
                'type': 'pattern',
            }).text = ' '.join('1' for _ in range(length + 1))
            ET.SubElement(segment_xml, 'cb', {
                'type': 'pattern',
            }).text = ' '.join('1' for _ in range(length))

    ET.SubElement(
        switchlist_xml,
        'switch',
        {
            'type': 'mux',
            'name': 'buffer',
            "R": "551",
            "Cin": ".77e-15",
            "Cout": "4e-15",
            # TODO: This value should be the "typical" pip switch delay from
            # This value is the dominate term in the inter-cluster delay
            # estimate.
            "Tdel": "0.178e-9",
            "mux_trans_size": "2.630740",
            "buf_size": "27.645901"
        }
    )

    device_xml = ET.SubElement(arch_xml, 'device')

    ET.SubElement(
        device_xml, 'sizing', {
            "R_minW_nmos": "6065.520020",
            "R_minW_pmos": "18138.500000",
        }
    )
    ET.SubElement(device_xml, 'area', {
        "grid_logic_tile_area": "14813.392",
    })
    ET.SubElement(
        device_xml, 'connection_block', {
            "input_switch_name": "buffer",
        }
    )
    ET.SubElement(device_xml, 'switch_block', {
        "type": "wilton",
        "fs": "3",
    })
    chan_width_distr_xml = ET.SubElement(device_xml, 'chan_width_distr')

    ET.SubElement(
        chan_width_distr_xml, 'x', {
            'distr': 'uniform',
            'peak': '1.0',
        }
    )
    ET.SubElement(
        chan_width_distr_xml, 'y', {
            'distr': 'uniform',
            'peak': '1.0',
        }
    )

    directlist_xml = ET.SubElement(arch_xml, 'directlist')

    pin_assignments = json.load(args.pin_assignments)

    # Choose smallest distance for block to block connections with multiple
    # direct_connections.  VPR cannot handle multiple block to block connections.
    directs = {}
    for direct in pin_assignments['direct_connections']:
        key = (direct['from_pin'], direct['to_pin'])

        if key not in directs:
            directs[key] = []

        directs[key].append(
            (abs(direct['x_offset']) + abs(direct['y_offset']), direct)
        )

    for direct in directs.values():
        _, direct = min(direct, key=lambda v: v[0])

        if direct['from_pin'].split('.')[0] not in tile_types:
            continue
        if direct['to_pin'].split('.')[0] not in tile_types:
            continue

        if direct['x_offset'] == 0 and direct['y_offset'] == 0:
            continue

        ET.SubElement(
            directlist_xml, 'direct', {
                'name':
                    '{}_to_{}_dx_{}_dy_{}'.format(
                        direct['from_pin'], direct['to_pin'],
                        direct['x_offset'], direct['y_offset']
                    ),
                'from_pin':
                    add_vpr_tile_prefix(direct['from_pin']),
                'to_pin':
                    add_vpr_tile_prefix(direct['to_pin']),
                'x_offset':
                    str(direct['x_offset']),
                'y_offset':
                    str(direct['y_offset']),
                'z_offset':
                    '0',
                'switch_name':
                    direct['switch_name'],
            }
        )

    arch_xml_str = ET.tostring(arch_xml, pretty_print=True).decode('utf-8')
    args.output_arch.write(arch_xml_str)
    args.output_arch.close()


if __name__ == '__main__':
    main()
