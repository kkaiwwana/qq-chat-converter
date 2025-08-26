import os
import sys
import argparse
import shutil


def parse_args():
    parser = argparse.ArgumentParser(description="Convert MHT files to JSON and HTML.")
    parser.add_argument(
        "mht_path",
        type=str,
        help="Path to the input MHT file."
    )
    parser.add_argument(
        "--out_dir",
        type=str,
        default=None,
        help="Output directory. Defaults to 'out_dir/<mht_file_name>' if not specified."
    )
    
    args = parser.parse_args()
    
    # Set default out_dir dynamically if not provided
    if args.out_dir is None:
        mht_file_name = os.path.splitext(os.path.basename(args.mht_path))[0]
        args.out_dir = os.path.join("out_dir", mht_file_name)
    
    return args


if __name__ == '__main__':
    sys.path.append(os.path.join(os.path.dirname(__file__), "../"))
    from qq_chat_converter import export_from_mht, deduplicate_images
    args = parse_args()
    
    # Ensure the output directory exists
    os.makedirs(args.out_dir, exist_ok=True)
    
    # Export MHT content
    export_from_mht(
        mht_path=args.mht_path,
        json_out=os.path.join(args.out_dir, "qq_chat.json"),
        html_out=os.path.join(args.out_dir, "qq_chat.html"),
        image_dir_name="Image"  # folder name only!
    )

    # Deduplicate images
    deduplicate_images(
        image_dir=os.path.join(args.out_dir, "Image"),
        json_file=os.path.join(args.out_dir, "qq_chat.json"),
        json_out=os.path.join(args.out_dir, "qq_chat.json")
    )
    
    # Copy index.html to the output directory
    index_html_src = os.path.join(os.path.dirname(__file__), "../qq_chat_converter/index.html")
    index_html_dst = os.path.join(args.out_dir, "index.html")
    shutil.copy(index_html_src, index_html_dst)