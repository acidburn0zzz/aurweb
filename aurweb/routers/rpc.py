from typing import List, Optional

from fastapi import APIRouter, Query, Request

from aurweb.rpc import RPC

router = APIRouter()


def arg_legacy_gen(request):
    # '[]' characters in the path randomly kept getting transformed to (what
    # appears to be) their HTML-formatted variants, so we keep that behavior
    # just in case.
    arguments = request.url.query.replace("%5B%5D", "[]").split("&")
    arguments.reverse()

    temp_args = []

    for i in arguments:
        # We only want to deal with 'arg' and 'arg[]' strings, so only take those.
        if i.split("=")[0] in ("arg", "arg[]"):
            temp_args += [i]

    returned_arguments = []
    argument_bracketed = False

    for i in temp_args:
        # Split argument on first occurance of '='.
        current_argument = i.split("=")

        argument_name = current_argument[0]
        argument_value = "".join(current_argument[1:])

        # Process argument.
        if argument_name == "arg[]":
            returned_arguments += [argument_value]
            argument_bracketed = True

        elif argument_name == "arg":
            # Only set this argument if 'arg[]' hasen't previously been found.
            if not argument_bracketed:
                returned_arguments = [argument_value]
            break

    return returned_arguments


@router.get("/rpc")
async def rpc(request: Request,
              v: Optional[int] = Query(None),
              type: Optional[str] = Query(None),
              arg: Optional[str] = Query(None),
              args: Optional[List[str]] = Query(None, alias="arg[]")):
    # Defaults for returned data
    returned_data = {}

    returned_data["version"] = v
    returned_data["results"] = []
    returned_data["resultcount"] = 0

    # Default the type field to "error", until we determine that
    # we're not erroneous (below).
    returned_data["type"] = "error"

    # Ensure valid version was passed
    if v is None:
        returned_data["error"] = "Please specify an API version."
        return returned_data
    elif v != 5:
        returned_data["error"] = "Invalid version specified."
        return returned_data
    else:
        # We got past initial error cases; set the type to what
        # the user gave us.
        returned_data["type"] = type

    # Take arguments from either 'args' or 'args[]' and put them into 'argument_list'.
    argument_list = []

    # In the PHP implementation, aurweb uses the last 'arg' value or all the
    # last 'arg[]' values when both 'arg' and 'arg[]' are part of the query
    # request. We thus preserve that behavior here for legacy purposes.
    if arg is not None and args is not None:
        argument_list = arg_legacy_gen(request)
    elif arg is not None:
        argument_list = [arg]
    elif args is not None:
        argument_list = args
    else:
        # Abort because no package arguments were passed.
        returned_data["type"] = "error"
        returned_data["error"] = "No request type/data specified."
        return returned_data

    # Process and return data
    returned_data = RPC(v=v,
                        type=type,
                        argument_list=argument_list,
                        returned_data=returned_data)

    return returned_data
