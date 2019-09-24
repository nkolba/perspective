# *****************************************************************************
#
# Copyright (c) 2019, the Perspective Authors.
#
# This file is part of the Perspective library, distributed under the terms of
# the Apache License 2.0.  The full license can be found in the LICENSE file.
#
from functools import wraps
from random import random
from perspective.table.libbinding import make_view_zero, make_view_one, make_view_two
from .view_config import ViewConfig
from ._data_formatter import to_format
from ._constants import COLUMN_SEPARATOR_STRING
from ._utils import _str_to_pythontype


class View(object):
    def __init__(self, Table, callbacks, config=None):
        '''Private constructor for a View object - use the Table.view() method to create Views.

        A View object represents a specific transform (configuration or pivot,
        filter, sort, etc) configuration on an underlying Table. A View
        receives all updates from the Table from which it is derived, and
        can be serialized to JSON or trigger a callback when it is updated.

        View objects are immutable, and will remain in memory and actively process
        updates until its delete() method is called.
        '''
        self._name = str(random())
        self._table = Table
        self._config = ViewConfig(config or {})
        self._sides = self.sides()

        date_validator = self._table._accessor._date_validator
        if self._sides == 0:
            self._view = make_view_zero(self._table._table, self._name, COLUMN_SEPARATOR_STRING, self._config, date_validator)
        elif self._sides == 1:
            self._view = make_view_one(self._table._table, self._name, COLUMN_SEPARATOR_STRING, self._config, date_validator)
        else:
            self._view = make_view_two(self._table._table, self._name, COLUMN_SEPARATOR_STRING, self._config, date_validator)

        self._column_only = self._view.is_column_only()
        self._callbacks = callbacks

    def get_config(self):
        '''Returns the original dictionary config passed in by the user.'''
        return self._config.get_config()

    def sides(self):
        '''How many pivoted sides does this View have?'''
        if len(self._config.get_row_pivots()) > 0 or len(self._config.get_column_pivots()) > 0:
            if len(self._config.get_column_pivots()) > 0:
                return 2
            else:
                return 1
        else:
            return 0

    def num_rows(self):
        '''The number of aggregated rows in the View. This is affected by the `row_pivots` that are applied to the View.

        Returns:
            int : number of rows
        '''
        return self._view.num_rows()

    def num_columns(self):
        '''The number of aggregated columns in the View. This is affected by the `column_pivots` that are applied to the View.

        Returns:
            int : number of columns
        '''
        return self._view.num_columns()

    def get_row_expanded(self, idx):
        '''Returns whether row at `idx` is expanded or collapsed.'''
        return self._view.get_row_expanded(idx)

    def expand(self, idx):
        '''Expands the row at 'idx', i.e. displaying its leaf rows.'''
        return self._view.expand(idx, len(self._config.get_row_pivots()))

    def collapse(self, idx):
        '''Collapses the row at 'idx', i.e. hiding its leaf rows'''
        return self._view.collapse(idx)

    def set_depth(self, depth):
        '''Sets the expansion depth of the pivot tree.'''
        return self._view.set_depth(depth, len(self._config.get_row_pivots()))

    def schema(self, as_string=False):
        '''The schema of this view, which is a key-value map that contains the column names and their Python data types.

        If the columns are aggregated, their aggregated types will be shown.

        Params:
            as_string (bool) : returns data types as string representations, if True

        Returns:
            schema : a map of strings to strings
        '''
        if as_string:
            return {item[0]: item[1] for item in self._view.schema().items()}

        return {item[0]: _str_to_pythontype(item[1]) for item in self._view.schema().items()}

    def on_update(self, callback, mode=None):
        mode = mode or "none"

        if not callable(callback):
            raise ValueError('Invalid callback - must be a callable function')

        if mode not in ["none", "cell", "row"]:
            raise ValueError('Invalid update mode {} - valid on_update modes are "none", "cell", or "row"'.format(mode))

        if mode == "cell" or mode == "row":
            if not self._view.get_deltas_enabled():
                self._view.set_deltas_enabled(True)

        # get deltas back from the view, and then call the user-defined callback
        def wrapped_callback(cache):
            if mode == "cell":
                if cache.get("step_delta") is None:
                    raise NotImplementedError("not implemented get_step_delta")
                callback(cache["step_delta"])
            elif mode == "row":
                if cache.get("row_delta") is None:
                    raise NotImplementedError("not implemented get_row_delta")
                callback(cache["row_delta"])
            else:
                callback()

        self._callbacks.add_callback({
            "name": self._name,
            "orig_callback": callback,
            "callback": wrapped_callback
        })

    def remove_update(self, callback):
        '''Given a callback function, remove it from the list of callbacks.

        Params:
            callback (func) : a callback that needs to be removed from the list of callbacks
        '''
        if not callable(callback):
            return ValueError("remove_update callback should be a callable function!")
        self._callbacks.remove_callbacks(lambda cb: cb["orig_callback"] != callback)

    def on_delete(self, callback):
        '''Set a callback to be run when the `delete()` method is called on the View.

        Params:
            callback (func) : a callback to run after the delete operation has completed
        '''
        if not callable(callback):
            return ValueError("on_delete callback must be a callable function!")
        self._delete_callback = callback

    def delete(self):
        '''Delete the view and clean up associated resources and references.'''
        self._table._views.pop(self._table._views.index(self._name))
        self._callbacks.remove_callbacks(lambda cb: cb["name"] != self._name)
        if hasattr(self, "_delete_callback"):
            self._delete_callback()

    def to_records(self, options=None):
        '''Serialize the view's dataset into a `list` of `dict`s containing each individual row.

        If the view is aggregated, the aggregated dataset will be returned.

        Params:
            options (dict) :
                user-provided options that specifies what data to return:
                - start_row: defaults to 0
                - end_row: defaults to the number of total rows in the view
                - start_col: defaults to 0
                - end_col: defaults to the total columns in the view
                - index: whether to return an implicit pkey for each row. Defaults to False
                - leaves_only: whether to return only the data at the end of the tree. Defaults to False

        Returns:
            list : A list of dictionaries, where each dict represents a new row of the dataset
        '''
        return to_format(options, self, 'records')

    def to_dict(self, options=None):
        '''Serialize the view's dataset into a `dict` of `str` keys and `list` values.
        Each key is a column name, and the associated value is the column's data packed into a list.

        If the view is aggregated, the aggregated dataset will be returned.

        Params:
            options (dict) :
                user-provided options that specifies what data to return:
                - start_row: defaults to 0
                - end_row: defaults to the number of total rows in the view
                - start_col: defaults to 0
                - end_col: defaults to the total columns in the view
                - index: whether to return an implicit pkey for each row. Defaults to False
                - leaves_only: whether to return only the data at the end of the tree. Defaults to False

        Returns:
            dict : a dictionary with string keys and list values, where key = column name and value = column values
        '''
        return to_format(options, self, 'dict')

    def to_numpy(self, options=None):
        '''Serialize the view's dataset into a `dict` of `str` keys and `numpy.array` values.
        Each key is a column name, and the associated value is the column's data packed into a numpy array.

        If the view is aggregated, the aggregated dataset will be returned.

        Params:
            options (dict) :
                user-provided options that specifies what data to return:
                - start_row: defaults to 0
                - end_row: defaults to the number of total rows in the view
                - start_col: defaults to 0
                - end_col: defaults to the total columns in the view
                - index: whether to return an implicit pkey for each row. Defaults to False
                - leaves_only: whether to return only the data at the end of the tree. Defaults to False

        Returns:
            dict : a dictionary with string keys and numpy array values, where key = column name and value = column values
        '''
        return to_format(options, self, 'numpy')

    def to_arrow(self, options=None):
        pass

    def to_df(self, options=None):
        '''Serialize the view's dataset into a pandas dataframe.

        If the view is aggregated, the aggregated dataset will be returned.

        Params:
            options (dict) :
                user-provided options that specifies what data to return:
                - start_row: defaults to 0
                - end_row: defaults to the number of total rows in the view
                - start_col: defaults to 0
                - end_col: defaults to the total columns in the view
                - index: whether to return an implicit pkey for each row. Defaults to False
                - leaves_only: whether to return only the data at the end of the tree. Defaults to False

        Returns:
            pandas.DataFrame : a pandas dataframe containing the serialized data.
        '''
        import pandas
        cols = self.to_numpy(options=options)
        return pandas.DataFrame(cols)

    @wraps(to_records)
    def to_json(self, options=None):
        return self.to_records(options)

    @wraps(to_dict)
    def to_columns(self, options=None):
        return self.to_dict(options)

    def _num_hidden_cols(self):
        '''Returns the number of columns that are sorted but not shown.'''
        hidden = 0
        columns = self._config.get_columns()
        for sort in self._config.get_sort():
            if sort[0] not in columns:
                hidden += 1
        return hidden

    def __del__(self):
        self.delete()