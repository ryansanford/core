
import pytest
from api import files


def test_extension():
    assert files.guess_type_from_filename('example.pdf') == 'pdf'

def test_multi_extension():
    assert files.guess_type_from_filename('example.zip') == 'archive'
    assert files.guess_type_from_filename('example.gephysio.zip') == 'gephysio'

def test_nifti():
    assert files.guess_type_from_filename('example.nii') == 'nifti'
    assert files.guess_type_from_filename('example.nii.gz') == 'nifti'
    assert files.guess_type_from_filename('example.nii.x.gz') == None

def test_qa():
    assert files.guess_type_from_filename('example.png') == 'image'
    assert files.guess_type_from_filename('example.qa.png') == 'qa'
    assert files.guess_type_from_filename('example.qa') == None
    assert files.guess_type_from_filename('example.qa.png.unknown') == None

def test_unknown():
    assert files.guess_type_from_filename('example.unknown') == None

def test_input_validation():
    with pytest.raises(Exception) as excinfo:
        files.move_form_file_field_into_cas(type('obj', (object,), {'hash': 'foo', 'path': ''}))
    assert 'Field is not a file field with hash and path' in str(excinfo.value)
    with pytest.raises(Exception) as excinfo:
        files.move_form_file_field_into_cas(type('obj', (object,), {'hash': '', 'path': 'foo'}))
    assert 'Field is not a file field with hash and path' in str(excinfo.value)